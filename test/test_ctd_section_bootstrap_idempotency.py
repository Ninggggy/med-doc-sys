import unittest
from datetime import datetime

from agent.agent_backend.services.ctd_section_bootstrap_service import CTDSectionBootstrapService
from agent.agent_backend.services.pre_review_service import PreReviewService


class _FakeCtdSections:
    def __init__(self) -> None:
        self._catalog = {
            "flat_sections": [
                {
                    "section_id": "2.3.s",
                    "section_code": "2.3.s",
                    "section_name": "原料药（名称，生产商）",
                    "root_section_id": "2",
                    "parent_section_id": "2.3",
                    "node_level": 2,
                    "sort_order": 2,
                    "is_leaf": True,
                    "title_path": ["模块2", "质量综述", "原料药（名称，生产商）"],
                    "concern_points": [],
                },
                {
                    "section_id": "2.3.s",
                    "section_code": "2.3.s",
                    "section_name": "原料药（名称，生产商）",
                    "root_section_id": "2",
                    "parent_section_id": "2.3",
                    "node_level": 2,
                    "sort_order": 2,
                    "is_leaf": True,
                    "title_path": ["模块2", "质量综述", "原料药（名称，生产商）"],
                    "concern_points": [],
                },
                {
                    "section_id": "2.3.p",
                    "section_code": "2.3.p",
                    "section_name": "制剂（名称，生产商）",
                    "root_section_id": "2",
                    "parent_section_id": "2.3",
                    "node_level": 2,
                    "sort_order": 3,
                    "is_leaf": True,
                    "title_path": ["模块2", "质量综述", "制剂（名称，生产商）"],
                    "concern_points": [],
                },
            ]
        }

    def get_catalog(self):
        return self._catalog


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return list(self._rows)


class _FakeSession:
    def __init__(self):
        self.new = []
        self.added = []

    def query(self, model):
        _ = model
        return _FakeQuery([])

    def add(self, row):
        row_key = (
            str(getattr(row, "project_id", "") or "").strip(),
            str(getattr(row, "section_id", "") or "").strip(),
        )
        if row_key in {
            (
                str(getattr(existing, "project_id", "") or "").strip(),
                str(getattr(existing, "section_id", "") or "").strip(),
            )
            for existing in self.added
        }:
            raise AssertionError(f"duplicate row inserted: {row_key}")
        self.added.append(row)


class CTDSectionBootstrapIdempotencyTests(unittest.TestCase):
    def test_bootstrap_service_skips_duplicate_flat_sections(self) -> None:
        service = object.__new__(CTDSectionBootstrapService)
        service.ctd_sections = _FakeCtdSections()
        service._now = lambda: datetime(2026, 3, 29, 0, 0, 0)
        session = _FakeSession()

        created = service._seed_missing_project_sections(session, "prj_test")

        self.assertEqual(created, 2)
        self.assertEqual(
            [row.section_id for row in session.added],
            ["2.3.s", "2.3.p"],
        )

    def test_pre_review_service_skips_duplicate_flat_sections(self) -> None:
        service = object.__new__(PreReviewService)
        service.ctd_sections = _FakeCtdSections()
        service._now = lambda: datetime(2026, 3, 29, 0, 0, 0)
        session = _FakeSession()

        service._ensure_project_sections(session, "prj_test")

        self.assertEqual(
            [row.section_id for row in session.added],
            ["2.3.s", "2.3.p"],
        )


if __name__ == "__main__":
    unittest.main()
