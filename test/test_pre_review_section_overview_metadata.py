import json
import unittest
from datetime import datetime

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from agent.agent_backend.database.mysql.db_model import (
    PreReviewProject,
    PreReviewSubmissionFile,
    PreReviewSubmissionSectionContent,
)
from agent.agent_backend.database.mysql.mysql_conn import Base
from agent.agent_backend.services.pre_review_submission_service import PreReviewSubmissionService


class _Connection:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def get_session(self):
        return self.session_factory()


class _Owner:
    def __init__(self, session_factory):
        self.db_conn = _Connection(session_factory)

    @staticmethod
    def _is_ctd_structure_project(_project):
        return True

    @staticmethod
    def _load_project_section_catalog(_session, _project_id):
        return {
            "all_sections": [
                {
                    "section_id": "3.2.p.2.3",
                    "section_code": "3.2.p.2.3",
                    "section_name": "生产工艺的开发",
                    "title_path": ["产品开发", "生产工艺的开发"],
                    "parent_section_id": "3.2.p.2",
                }
            ],
            "flat_sections": [],
        }


class PreReviewSectionOverviewMetadataTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(
            self.engine,
            tables=[
                PreReviewProject.__table__,
                PreReviewSubmissionFile.__table__,
                PreReviewSubmissionSectionContent.__table__,
            ],
        )
        self.Session = sessionmaker(bind=self.engine, expire_on_commit=False)
        now = datetime(2026, 8, 18, 12, 0, 0)
        session = self.Session()
        try:
            session.add(
                PreReviewProject(
                    project_id="project-overview",
                    project_name="overview",
                    status="completed",
                    create_time=now,
                    update_time=now,
                    is_deleted=False,
                )
            )
            session.add(
                PreReviewSubmissionFile(
                    doc_id="doc-overview",
                    project_id="project-overview",
                    file_name="large.docx",
                    file_path="/tmp/large.docx",
                    file_type="docx",
                    material_category="pharmacy",
                    section_id="3.2.p.2",
                    is_chunked=True,
                    chunk_size=2,
                    is_deleted=False,
                    create_time=now,
                )
            )
            session.add_all(
                [
                    PreReviewSubmissionSectionContent(
                        doc_id="doc-overview",
                        project_id="project-overview",
                        section_id="3.2.p.2.3",
                        section_code="3.2.p.2.3",
                        section_name="生产工艺的开发",
                        chunk_index=1,
                        content="长正文甲" * 250000,
                        content_preview="开头摘要",
                        source_parser="ctd_docx_markdown",
                        create_time=now,
                        update_time=now,
                    ),
                    PreReviewSubmissionSectionContent(
                        doc_id="doc-overview",
                        project_id="project-overview",
                        section_id="3.2.p.2.3",
                        section_code="3.2.p.2.3",
                        section_name="生产工艺的开发",
                        chunk_index=2,
                        content="长正文乙" * 250000,
                        content_preview="",
                        source_parser="ctd_docx_markdown",
                        create_time=now,
                        update_time=now,
                    ),
                ]
            )
            session.commit()
        finally:
            session.close()
        self.service = PreReviewSubmissionService(_Owner(self.Session))

    def tearDown(self):
        self.engine.dispose()

    def test_overview_uses_sql_aggregation_and_never_returns_full_text(self):
        statements = []

        def capture_statement(_conn, _cursor, statement, _parameters, _context, _executemany):
            statements.append(statement.lower())

        event.listen(self.engine, "before_cursor_execute", capture_statement)
        try:
            ok, message, payload = self.service.get_submission_section_overview(
                "project-overview",
                "doc-overview",
            )
        finally:
            event.remove(self.engine, "before_cursor_execute", capture_statement)

        self.assertTrue(ok, message)
        self.assertTrue(payload["content_ready"])
        self.assertEqual(len(payload["sections"]), 1)
        section = payload["sections"][0]
        self.assertEqual(
            set(section),
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
        self.assertEqual(section["char_count"], 2_000_000)
        self.assertEqual(section["content_preview"], "开头摘要")
        self.assertNotIn("raw_content", section)
        self.assertNotIn("cleaned_markdown", section)
        response_size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        self.assertLess(response_size, 2 * 1024)
        aggregate_sql = [item for item in statements if "group by" in item]
        self.assertEqual(len(aggregate_sql), 1)
        self.assertIn("sum(length(", aggregate_sql[0])


if __name__ == "__main__":
    unittest.main()
