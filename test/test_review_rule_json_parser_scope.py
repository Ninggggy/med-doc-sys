import unittest
from pathlib import Path

from agent.agent_backend.services.ctd_section_service import CTDSectionService
from agent.agent_backend.utils.parser.review_rule_json_parser import ReviewRuleJsonParser


RAW_DATA_DIR = Path(__file__).resolve().parents[1] / "agent_backend" / "data" / "raw_data"


class ReviewRuleJsonParserScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.parser = ReviewRuleJsonParser(CTDSectionService(raw_data_dir=str(RAW_DATA_DIR)))

    def test_child_nodes_inside_review_rules_do_not_leak_into_parent(self) -> None:
        rows = self.parser.parse_payload(
            {
                "section_id": "3.2.p.2",
                "section_name": "产品开发",
                "review_rules": [
                    {
                        "section_id": "3.2.p.2.1",
                        "section_name": "处方组成",
                        "review_rules": ["子章节规则 A"],
                    },
                    {
                        "section_id": "3.2.p.2.2",
                        "section_name": "制剂",
                        "concern_points": ["子章节规则 B"],
                    },
                ],
            }
        )

        by_section = {item["section_id"]: item["review_rules"] for item in rows}
        self.assertNotIn("3.2.p.2", by_section)
        self.assertEqual(by_section["3.2.p.2.1"], ["子章节规则 A"])
        self.assertEqual(by_section["3.2.p.2.2"], ["子章节规则 B"])

    def test_mixed_parent_rule_and_child_node_remain_in_their_own_sections(self) -> None:
        rows = self.parser.parse_payload(
            {
                "section_id": "3.2.p.2",
                "review_rules": [
                    {"rule_text": "父章节规则"},
                    {"section_id": "3.2.p.2.1", "rule_text": "子章节规则"},
                ],
            }
        )

        by_section = {item["section_id"]: item["review_rules"] for item in rows}
        self.assertEqual(by_section["3.2.p.2"], ["父章节规则"])
        self.assertEqual(by_section["3.2.p.2.1"], ["子章节规则"])

    def test_scalar_parent_rule_and_child_node_can_share_one_list(self) -> None:
        rows = self.parser.parse_payload(
            {
                "section_id": "3.2.p.2",
                "review_rules": [
                    "父章节字符串规则",
                    {
                        "section_id": "3.2.p.2.1",
                        "section_name": "处方组成",
                        "rule_text": "子章节对象规则",
                    },
                ],
            }
        )

        by_section = {item["section_id"]: item["review_rules"] for item in rows}
        self.assertEqual(by_section["3.2.p.2"], ["父章节字符串规则"])
        self.assertEqual(by_section["3.2.p.2.1"], ["子章节对象规则"])


if __name__ == "__main__":
    unittest.main()
