import unittest
from datetime import datetime

from agent.agent_backend.database.mysql.db_model import PreReviewSectionRule
from agent.agent_backend.services.pre_review_service import PreReviewService


class _FakeCtdSections:
    def get_catalog(self):
        return {
            "all_sections": [
                {
                    "section_id": "3.2.p.1",
                    "section_name": "Dosage form",
                }
            ]
        }

    @staticmethod
    def normalize_section_id(section_id: str) -> str:
        return str(section_id or "").strip()


class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return list(self._rows)


class _FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.added = []
        self.deleted = []

    def query(self, model):
        _ = model
        return _FakeQuery(self.rows)

    def add(self, row):
        row_key = (
            str(getattr(row, "project_id", "") or "").strip(),
            str(getattr(row, "section_id", "") or "").strip(),
            str(getattr(row, "rule_code", "") or "").strip(),
        )
        for existing in self.rows:
            existing_key = (
                str(getattr(existing, "project_id", "") or "").strip(),
                str(getattr(existing, "section_id", "") or "").strip(),
                str(getattr(existing, "rule_code", "") or "").strip(),
            )
            if existing_key == row_key:
                raise AssertionError(f"duplicate key inserted: {row_key}")
        self.rows.append(row)
        self.added.append(row)

    def delete(self, row):
        self.deleted.append(row)
        self.rows.remove(row)

    def commit(self):
        return None

    def rollback(self):
        return None

    def close(self):
        return None


class PreReviewSectionRuleSeedMapTests(unittest.TestCase):
    def test_replace_global_seed_section_rule_map_updates_existing_rows(self) -> None:
        service = object.__new__(PreReviewService)
        service.ctd_sections = _FakeCtdSections()
        service._ensure_global_rule_project = lambda session: "__global_section_rules__"
        service._now = lambda: datetime(2026, 3, 29, 0, 0, 0)
        service._project_section_catalog_cache = {}

        existing_manual_row = PreReviewSectionRule(
            rule_id="section_rule_existing_manual",
            project_id="__global_section_rules__",
            section_id="3.2.p.1",
            section_name="Old name",
            rule_code="ctd_rule__3_2_p_1__34480cd4__01",
            rule_text="Old rule",
            source_type="manual",
            source_ref="legacy_manual",
            is_active=True,
            payload_json='{"scope_metadata":{"registration_scope":"","registration_class":"","registration_class_sub":"","module":"","section_path_prefix":""}}',
            create_time=datetime(2026, 3, 28, 0, 0, 0),
            update_time=datetime(2026, 3, 28, 0, 0, 0),
        )
        obsolete_import_row = PreReviewSectionRule(
            rule_id="section_rule_obsolete_import",
            project_id="__global_section_rules__",
            section_id="3.2.p.1",
            section_name="Dosage form",
            rule_code="ctd_rule__3_2_p_1__99",
            rule_text="Obsolete rule",
            source_type="json_import",
            source_ref="old_import",
            is_active=True,
            payload_json='{"scope_metadata":{"registration_scope":"","registration_class":"","registration_class_sub":"","module":"","section_path_prefix":""}}',
            create_time=datetime(2026, 3, 28, 0, 0, 0),
            update_time=datetime(2026, 3, 28, 0, 0, 0),
        )
        session = _FakeSession([existing_manual_row, obsolete_import_row])

        service._replace_global_seed_section_rule_map(
            session,
            {"3.2.p.1": ["The formulation should list each component and ratio."]},
            source_ref="uploaded_json",
        )

        self.assertEqual(len(session.added), 0)
        self.assertIn(obsolete_import_row, session.deleted)
        self.assertEqual(existing_manual_row.source_type, "json_import")
        self.assertEqual(existing_manual_row.source_ref, "uploaded_json")
        self.assertEqual(existing_manual_row.section_name, "Dosage form")
        self.assertEqual(existing_manual_row.rule_text, "The formulation should list each component and ratio.")
        self.assertEqual(existing_manual_row.update_time, datetime(2026, 3, 29, 0, 0, 0))


if __name__ == "__main__":
    unittest.main()
