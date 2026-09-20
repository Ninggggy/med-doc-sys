import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.agent_backend.services.knowledge_service import KnowledgeService


class KnowledgeReadPathRegressionTests(unittest.TestCase):
    def test_keyword_search_streams_large_parsed_json_and_supports_unicode_escape(self):
        service = object.__new__(KnowledgeService)
        with tempfile.TemporaryDirectory() as temp_dir:
            parsed_path = Path(temp_dir) / "doc-large.json"
            parsed_path.write_text(
                json.dumps(
                    {"body": "A" * (128 * 1024) + "稳定性研究关键词"},
                    ensure_ascii=True,
                ),
                encoding="utf-8",
            )
            with patch(
                "agent.agent_backend.services.knowledge_service.PARSED_DIR",
                temp_dir,
            ):
                self.assertTrue(
                    service._keyword_match_in_parsed(
                        "doc-large",
                        "稳定性研究关键词",
                    )
                )
                self.assertFalse(service._keyword_match_in_parsed("doc-large", "不存在的内容"))


if __name__ == "__main__":
    unittest.main()
