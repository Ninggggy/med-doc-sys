import unittest
from pathlib import Path
from tempfile import NamedTemporaryFile

from agent.agent_backend.script.split_ctd_markdown_to_ectd import SECTION_HEADING_RE
from agent.agent_backend.services.ctd_section_service import CTDSectionService
from agent.agent_backend.services.pre_review_service import PreReviewService
from agent.agent_backend.services.pre_review_submission_ctd_service import PreReviewSubmissionCTDService
from agent.agent_backend.utils.parser.ctd_ocr_parser import _normalize_section_id as normalize_ocr_section_id
from agent.agent_backend.utils.parser.docx_markdown_parser import extract_heading_info


RAW_DATA_DIR = Path(__file__).resolve().parents[1] / "agent_backend" / "data" / "raw_data"


class _OwnerStub:
    def __init__(self) -> None:
        self.ctd_sections = CTDSectionService(raw_data_dir=str(RAW_DATA_DIR))

    def _branch_root_from_section_id(self, section_id: str) -> str:
        value = self.ctd_sections.normalize_section_id(section_id)
        if not value:
            return ""
        if value == "3" and self.ctd_sections.get_section("3.2"):
            return "3.2"
        section_meta = self.ctd_sections.get_section(value, leaf_only=False)
        if not isinstance(section_meta, dict):
            return value
        node_level = int(section_meta.get("node_level", 0) or 0)
        if node_level <= 2:
            return value
        parent_section_id = str(section_meta.get("parent_section_id", "") or "").strip()
        return self.ctd_sections.normalize_section_id(parent_section_id) if parent_section_id else value


class CTDUploadRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ctd = CTDSectionService(raw_data_dir=str(RAW_DATA_DIR))
        cls.owner = _OwnerStub()
        cls.ctd_upload = PreReviewSubmissionCTDService(cls.owner)
        temp = NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
        temp.write(
            "# 3.2.s.1 basic info\n\n"
            "## 3.2.s.1.1 drug name\n\n"
            "# 3.2.p.1 dosage form\n\n"
            "## 3.2.p.2 product development\n"
        )
        temp.flush()
        temp.close()
        cls.sample_ctd_md = temp.name

    @classmethod
    def tearDownClass(cls) -> None:
        if getattr(cls, "sample_ctd_md", ""):
            try:
                Path(cls.sample_ctd_md).unlink(missing_ok=True)
            except Exception:
                pass

    def test_section_ids_use_lowercase_canonical_form(self) -> None:
        self.assertEqual(self.ctd.normalize_section_id("3.2.S.1"), "3.2.s.1")
        self.assertEqual(self.ctd.normalize_section_id("3.2.P.8.3"), "3.2.p.8.3")

    def test_parser_helpers_keep_ctd_heading_ids_lowercase(self) -> None:
        self.assertIsNotNone(SECTION_HEADING_RE.match("### 3.2.s.2.2 production control"))
        self.assertIsNotNone(SECTION_HEADING_RE.match("### 3.2.p.5.1 quality standard"))
        self.assertEqual(normalize_ocr_section_id("3.2.S.1"), "3.2.s.1")
        self.assertEqual(normalize_ocr_section_id("3.2.P.8.3"), "3.2.p.8.3")
        heading = extract_heading_info("3.2.S.1 basic info")
        self.assertIsNotNone(heading)
        self.assertEqual(heading.number, "3.2.s.1")

    def test_module3_body_root_exists_in_catalog(self) -> None:
        section = self.ctd.get_section("3.2")
        self.assertIsInstance(section, dict)
        self.assertEqual(self.ctd.get_section("3.2.s", leaf_only=False)["parent_section_id"], "3.2")

    def test_module3_upload_root_resolves_to_branch_roots(self) -> None:
        roots = self.ctd_upload.resolve_single_ctd_split_roots(
            file_path=self.sample_ctd_md,
            requested_section_id="3",
            catalog=self.ctd.get_catalog(),
            file_type="md",
        )
        self.assertCountEqual(roots, ["3.2.s", "3.2.p"])

    def test_body_directory_resolves_to_branch_roots(self) -> None:
        roots = self.ctd_upload.resolve_single_ctd_split_roots(
            file_path=self.sample_ctd_md,
            requested_section_id="3.2",
            catalog=self.ctd.get_catalog(),
            file_type="md",
        )
        self.assertCountEqual(roots, ["3.2.s", "3.2.p"])

    def test_structured_ctd_payload_normalizes_to_leaf_sections(self) -> None:
        service = object.__new__(PreReviewService)
        service.ctd_sections = self.ctd

        payload = {
            "structure_type": "ctd_api_markdown_json",
            "sections": [
                {
                    "section_id": "3.2.s.1",
                    "section_name": "basic info",
                    "content": "directory content",
                },
                {
                    "section_id": "3.2.s.1.1",
                    "section_name": "drug name",
                    "content": "drug name content",
                    "title_path": ["basic info", "drug name"],
                },
                {
                    "section_id": "3.2.s.2",
                    "section_name": "process control",
                    "content": "process control content",
                    "title_path": ["production", "process control"],
                },
            ],
            "review_units": [
                {
                    "chunk_id": "3.2.s.1.1",
                    "section_id": "3.2.s.1.1",
                    "section_code": "3.2.s.1.1",
                    "section_name": "drug name",
                    "text": "drug name content",
                },
                {
                    "chunk_id": "3.2.s.2",
                    "section_id": "3.2.s.2",
                    "section_code": "3.2.s.2",
                    "section_name": "process control",
                    "text": "process control content",
                },
            ],
        }

        normalized = service._normalize_ctd_payload_to_leaf_sections(payload, "3.2")
        self.assertEqual(normalized["root_section_id"], "3.2")
        self.assertIn("catalog_aligned_v2", normalized["structure_type"])
        section_ids = [item["section_id"] for item in normalized["sections"]]
        unit_ids = [item["section_id"] for item in normalized["review_units"]]
        self.assertCountEqual(section_ids, ["3.2.s.1", "3.2.s.1.1", "3.2.s.2"])
        self.assertCountEqual(unit_ids, section_ids)
        by_section = {item["section_id"]: item for item in normalized["sections"]}
        self.assertEqual(by_section["3.2.s.1.1"]["content"], "drug name content")
        self.assertEqual(by_section["3.2.s.1"]["content"], "directory content")
        self.assertEqual(by_section["3.2.s.2"]["content"], "process control content")
        self.assertEqual(by_section["3.2.s.1"]["section_name"], self.ctd.get_section("3.2.s.1")["section_name"])

    def test_docx_nodes_keep_nested_headings_as_headings(self) -> None:
        rendered = self.ctd_upload._render_docx_node_block(
            node={
                "content": "3.2.s.3.1 structure confirmation\n\nBody paragraph.",
                "images": [],
                "level": 4,
            },
            current_ctd_section_id="",
            current_ctd_section_name="",
            current_title="3.2.s.3.1 structure confirmation",
            explicit_ctd_heading=False,
        )
        self.assertTrue(rendered.startswith("#### 3.2.s.3.1 structure confirmation"))
        self.assertEqual(rendered.count("3.2.s.3.1 structure confirmation"), 1)

    def test_docx_blocks_attach_unmapped_next_level_titles_to_parent(self) -> None:
        blocks = self.ctd_upload._collect_docx_blocks_by_ctd_section(
            nodes=[
                {
                    "section_title": "3.2.s.3 特性鉴定",
                    "title": "3.2.s.3 特性鉴定",
                    "level": 3,
                    "content": "Intro paragraph.",
                    "children_sections": [
                        {
                            "section_title": "3.2.s.3.1 structure confirmation",
                            "title": "3.2.s.3.1 structure confirmation",
                            "level": 4,
                            "content": "3.2.s.3.1 structure confirmation\n\nBody paragraph.",
                            "children_sections": [],
                        }
                    ],
                }
            ],
            allowed_section_ids={
                "3.2.s.3": {
                    "section_id": "3.2.s.3",
                    "section_name": "特性鉴定",
                    "title_path": ["特性鉴定"],
                    "is_leaf": True,
                }
            },
        )
        self.assertIn("3.2.s.3", blocks)
        rendered = "\n\n".join(blocks["3.2.s.3"])
        self.assertIn("3.2.s.3.1 structure confirmation", rendered)
        self.assertEqual(rendered.count("3.2.s.3.1 structure confirmation"), 1)
        self.assertNotIn("3.2.s.3 structure confirmation", rendered)

    def test_ambiguous_title_is_preserved_on_parent_and_reported(self) -> None:
        diagnostics = []
        blocks = self.ctd_upload._collect_docx_blocks_by_ctd_section(
            nodes=[
                {
                    "section_title": "包装系统",
                    "level": 4,
                    "content": "这段原文不能丢失。",
                    "children_sections": [],
                    "source": {"paragraph_index": 12},
                }
            ],
            allowed_section_ids={
                "3.2.p.2": {"section_id": "3.2.p.2", "section_name": "产品开发", "title_path": ["产品开发"], "is_leaf": False},
                "3.2.p.2.4": {"section_id": "3.2.p.2.4", "section_name": "包装系统", "title_path": ["产品开发", "包装系统"], "is_leaf": True},
                "3.2.p.2.7": {"section_id": "3.2.p.2.7", "section_name": "包装系统", "title_path": ["产品开发", "包装系统"], "is_leaf": True},
            },
            inherited_section_id="3.2.p.2",
            inherited_section_name="产品开发",
            mapping_diagnostics=diagnostics,
        )

        self.assertIn("3.2.p.2", blocks)
        rendered = "\n".join(blocks["3.2.p.2"])
        self.assertIn("这段原文不能丢失", rendered)
        self.assertIn("解析待确认", rendered)
        self.assertEqual(diagnostics[0]["type"], "ambiguous_heading")
        self.assertEqual(set(diagnostics[0]["candidate_section_ids"]), {"3.2.p.2.4", "3.2.p.2.7"})

    def test_embedded_unmatched_heading_and_body_are_preserved(self) -> None:
        diagnostics = []
        blocks = self.ctd_upload._collect_docx_blocks_by_ctd_section(
            nodes=[
                {
                    "section_title": "3.2.p.2 产品开发",
                    "level": 3,
                    "content": (
                        "父章节导语。\n\n"
                        "3.2.p.9.9 交接文档自定义子章节\n\n"
                        "该子章节正文必须保留。\n\n"
                        "1.2 内部试验标题\n\n"
                        "内部试验正文也必须保留。"
                    ),
                    "children_sections": [],
                    "source": {"paragraph_index": 30},
                }
            ],
            allowed_section_ids={
                "3.2.p.2": {
                    "section_id": "3.2.p.2",
                    "section_name": "产品开发",
                    "title_path": ["产品开发"],
                    "is_leaf": False,
                }
            },
            inherited_section_id="3.2.p.2",
            inherited_section_name="产品开发",
            mapping_diagnostics=diagnostics,
        )

        rendered = "\n".join(blocks["3.2.p.2"])
        self.assertIn("父章节导语", rendered)
        self.assertIn("该子章节正文必须保留", rendered)
        self.assertIn("内部试验正文也必须保留", rendered)
        self.assertGreaterEqual(rendered.count("解析待确认"), 2)
        self.assertEqual(
            {item["type"] for item in diagnostics},
            {"unmatched_ctd_heading", "non_ctd_numbered_heading"},
        )
        self.assertTrue(all(item["preserved_under_section_id"] == "3.2.p.2" for item in diagnostics))
        self.assertTrue(all("content_line_index" in item["source"] for item in diagnostics))


if __name__ == "__main__":
    unittest.main()
