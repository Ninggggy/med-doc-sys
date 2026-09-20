import unittest
from types import SimpleNamespace

from agent.agent_backend.database.mysql.db_model import FileInfo
from agent.agent_backend.services.cde_guideline_crawler import CDEGuidelineCrawler


class _Query:
    def __init__(self, rows):
        self._rows = list(rows)

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return list(self._rows)


class _Session:
    def __init__(self, rows):
        self._rows = list(rows)
        self.queried_model = None
        self.closed = False

    def query(self, model):
        self.queried_model = model
        return _Query(self._rows)

    def close(self):
        self.closed = True


class _Connection:
    def __init__(self, session):
        self._session = session

    def get_session(self):
        return self._session


class CDEGuidelineCompareTests(unittest.TestCase):
    def test_compare_with_local_uses_file_info_without_network_access(self) -> None:
        session = _Session(
            [
                SimpleNamespace(
                    file_name="Current Guideline.pdf",
                    registration_path="2026-01-01",
                    doc_id="doc_current",
                ),
                SimpleNamespace(
                    file_name="Updated Guideline.pdf",
                    registration_path="2025-01-01",
                    doc_id="doc_updated",
                ),
            ]
        )
        crawler = object.__new__(CDEGuidelineCrawler)
        crawler._db_conn = _Connection(session)

        result = crawler.compare_with_local(
            [
                {"title": "Current Guideline", "issueDate": "2026-01-01"},
                {"title": "Updated Guideline", "issueDate": "2026-06-01"},
                {"title": "New Guideline", "issueDate": "2026-07-01"},
            ]
        )

        self.assertIs(session.queried_model, FileInfo)
        self.assertTrue(session.closed)
        self.assertEqual([item["title"] for item in result["existing"]], ["Current Guideline"])
        self.assertEqual([item["title"] for item in result["updated"]], ["Updated Guideline"])
        self.assertEqual([item["title"] for item in result["new"]], ["New Guideline"])
        self.assertEqual(result["updated"][0]["local_doc_id"], "doc_updated")
        self.assertEqual(result["total_cde"], 3)
        self.assertEqual(result["total_local"], 2)


if __name__ == "__main__":
    unittest.main()
