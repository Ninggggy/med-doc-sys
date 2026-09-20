import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT_DIR = Path(__file__).resolve().parents[1]
PACKAGE_PARENT = ROOT_DIR.parent
if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))


from agent.agent_backend.services.filing_change_form_parser_service import FilingChangeFormParserService


class FilingChangeFormPdfParserTests(unittest.TestCase):
    def test_parse_pdf_form_maps_items_into_existing_schema(self) -> None:
        service = FilingChangeFormParserService()
        fake_result = {
            "raw_text": (
                "6. 药品通用名称: 注射用示例药\n"
                "15. 药品有效期 原有效期: 24个月 拟延长后有效期: 36个月 贮藏条件: 阴凉处保存\n"
                "21. 补充申请的内容: 延长药品有效期至36个月\n"
                "22. 提出补充申请的理由: 根据稳定性研究结果提出申请\n"
            ),
            "extra_blocks": {"其他特别申明事项": "本申请仅用于测试 PDF 解析回填。"},
            "items": [
                {"item_no": 1, "raw_text": "境内生产药品补充申请", "subfields": {}, "tables": []},
                {"item_no": 6, "raw_text": "注射用示例药", "subfields": {}, "tables": []},
                {
                    "item_no": 15,
                    "raw_text": "原有效期: 24个月\n拟延长后有效期: 36个月\n贮藏条件: 阴凉处保存",
                    "subfields": {},
                    "tables": [],
                },
                {"item_no": 21, "raw_text": "延长药品有效期至36个月", "subfields": {}, "tables": []},
                {"item_no": 22, "raw_text": "根据稳定性研究结果提出申请", "subfields": {}, "tables": []},
                {
                    "item_no": 30,
                    "raw_text": "中文名称: 海南示例制药有限公司\n联系人: 张三\n电话: 13800000000\n通讯地址: 海口市测试路1号",
                    "subfields": {
                        "中文名称": "海南示例制药有限公司",
                        "联系人": "张三",
                        "电话": "13800000000",
                        "通讯地址": "海口市测试路1号",
                    },
                    "tables": [],
                },
            ],
            "tables": [],
        }

        with patch(
            "agent.agent_backend.services.filing_change_form_parser_service.parse_drug_supplement_pdf",
            return_value=fake_result,
        ):
            result = service.parse_form_file(str(ROOT_DIR / "task" / "change_review" / "申请表模板.pdf"))

        form_json = result["form_json"]
        self.assertEqual(form_json["item_1_application_type"]["value"], "境内生产药品补充申请")
        self.assertEqual(form_json["item_6_generic_name"]["value"], "注射用示例药")
        self.assertEqual(form_json["item_15_validity_period"]["sub_fields"]["original_validity_period"], "24个月")
        self.assertEqual(form_json["item_15_validity_period"]["sub_fields"]["proposed_validity_period"], "36个月")
        self.assertEqual(form_json["item_15_validity_period"]["sub_fields"]["storage_condition"], "阴凉处保存")
        self.assertEqual(form_json["item_21_change_content"]["value"], "延长药品有效期至36个月")
        self.assertEqual(form_json["item_22_change_reason"]["value"], "根据稳定性研究结果提出申请")
        self.assertEqual(form_json["item_30_applicant_info"]["sub_fields"]["applicant_name"], "海南示例制药有限公司")
        self.assertIn("张三", form_json["item_30_applicant_info"]["sub_fields"]["contact"])
        self.assertEqual(form_json["item_30_applicant_info"]["sub_fields"]["mailing_address"], "海口市测试路1号")
        self.assertEqual(form_json["item_30_applicant_info"]["sub_fields"]["address"], "")
        self.assertEqual(form_json["item_30_applicant_info"]["sub_fields"]["phone"], "13800000000")
        self.assertEqual(form_json["special_statement"]["value"], "本申请仅用于测试 PDF 解析回填。")

    def test_parse_pdf_form_preserves_explicit_names_without_drug_specific_inference(self) -> None:
        service = FilingChangeFormParserService()
        fake_result = {
            "raw_text": (
                "6. 药品通用名称: AK ui Hb as\n"
                "7. 英文名称/拉丁名称: Minoxidi) Liniment\n"
                "8. 汉语拼音: Minuodier Chaji\n"
                "11. 剂型: 中 国药 典 剂 型 : 片 剂\n"
            ),
            "extra_blocks": {},
            "items": [
                {"item_no": 6, "raw_text": "AK ui Hb as", "subfields": {}, "tables": [], "source_regions": []},
                {"item_no": 7, "raw_text": "Minoxidi) Liniment", "subfields": {}, "tables": [], "source_regions": []},
                {"item_no": 8, "raw_text": "Minuodier Chaji", "subfields": {}, "tables": [], "source_regions": []},
                {"item_no": 11, "raw_text": "中 国药 典 剂 型 : 片 剂", "subfields": {}, "tables": [], "source_regions": []},
            ],
            "tables": [],
        }

        with patch(
            "agent.agent_backend.services.filing_change_form_parser_service.parse_drug_supplement_pdf",
            return_value=fake_result,
        ):
            result = service.parse_form_file(str(ROOT_DIR / "task" / "change_review" / "申请表模板.pdf"))

        form_json = result["form_json"]
        self.assertEqual(form_json["item_6_generic_name"]["value"], "AK ui Hb as")
        self.assertEqual(form_json["item_7_english_or_latin_name"]["value"], "Minoxidi) Liniment")
        self.assertEqual(form_json["item_11_dosage_form"]["selected_values"], ["片剂"])

    def test_parse_pdf_form_does_not_borrow_packaging_from_validity_region(self) -> None:
        service = FilingChangeFormParserService()
        fake_result = {
            "raw_text": "15. 药品有效期: 36个月\n16. 处方:\n",
            "extra_blocks": {},
            "items": [
                {
                    "item_no": 15,
                    "raw_text": "36个月",
                    "subfields": {},
                    "tables": [],
                    "source_regions": [{"page": 2, "bbox_pdf": [60.8, 460.4, 212.4, 476.0]}],
                },
                {
                    "item_no": 16,
                    "raw_text": "",
                    "subfields": {},
                    "tables": [],
                    "source_regions": [{"page": 2, "bbox_pdf": [60.8, 479.2, 333.6, 512.4]}],
                },
            ],
            "tables": [],
        }

        def fake_region_ocr(_file_path, region, **kwargs):
            bbox = region.get("bbox_pdf", [])
            if bbox and bbox[1] < 460.4:
                return "14. 包装: 直接接触药品的包装材料和容器: 铝塑包装\n包装规格: 10片/板"
            return "16. 处方: 活性成份/中药成份/.. : 米诺地尔\n辅料: 纯化水"

        with patch(
            "agent.agent_backend.services.filing_change_form_parser_service.parse_drug_supplement_pdf",
            return_value=fake_result,
        ), patch(
            "agent.agent_backend.services.filing_change_form_parser_service.ocr_pdf_region",
            side_effect=fake_region_ocr,
        ):
            result = service.parse_form_file(str(ROOT_DIR / "task" / "change_review" / "申请表模板.pdf"))

        form_json = result["form_json"]
        self.assertEqual(form_json["item_14_packaging"]["sub_fields"]["packaging_specification"], "")
        self.assertEqual(form_json["item_16_prescription"]["sub_fields"]["active_ingredients"], "")
        self.assertEqual(form_json["item_16_prescription"]["sub_fields"]["excipients"], "")

    def test_parse_pdf_form_can_sanitize_polluted_and_noisy_fields(self) -> None:
        service = FilingChangeFormParserService()
        fake_result = {
            "raw_text": "",
            "extra_blocks": {},
            "items": [
                {
                    "item_no": 13,
                    "raw_text": "14. 包装: 直接接触药品的包装材料和容器: 铝塑包装\n包装规格: 10片/板",
                    "subfields": {"包装规格": "10片/板"},
                    "tables": [],
                    "source_regions": [],
                },
                {
                    "item_no": 19,
                    "raw_text": "个",
                    "subfields": {},
                    "tables": [],
                    "source_regions": [],
                },
                {
                    "item_no": 20,
                    "raw_text": "WIE DR: WEA GD\n治疗 脱发",
                    "subfields": {},
                    "tables": [],
                    "source_regions": [],
                },
                {
                    "item_no": 24,
                    "raw_text": "起 利 权 属 志明, 我 们 声明 : 本 中 请 对 他 人 专 利 不 构成 侵 权 .",
                    "subfields": {},
                    "tables": [],
                    "source_regions": [],
                },
                {
                    "item_no": 25,
                    "raw_text": "是 否 涉及 数据 保护 : 是 8",
                    "subfields": {"是否涉及数据保护": "是 8"},
                    "tables": [],
                    "source_regions": [],
                },
                {
                    "item_no": 28,
                    "raw_text": "首次 申请",
                    "subfields": {},
                    "tables": [],
                    "source_regions": [],
                },
                {
                    "item_no": 29,
                    "raw_text": "备注:\n临床试\n受理号 批件号 批准内容 复/终止情况/不适用",
                    "subfields": {},
                    "tables": [],
                    "source_regions": [],
                },
            ],
            "tables": [],
        }

        with patch(
            "agent.agent_backend.services.filing_change_form_parser_service.parse_drug_supplement_pdf",
            return_value=fake_result,
        ):
            result = service.parse_form_file(str(ROOT_DIR / "task" / "change_review" / "申请表模板.pdf"))

        form_json = result["form_json"]
        self.assertEqual(form_json["item_13_other_specifications"]["value"], "")
        self.assertEqual(form_json["item_19_pre_acceptance_inspection"]["parse_confidence"], 0.0)
        # 无坐标/标签依据时不能把疑似噪声直接删掉；保留来源并要求人工核对。
        self.assertEqual(form_json["item_20_indication_or_function"]["sub_fields"]["description"], "WIE DR: WEA GD\n治疗 脱发")
        self.assertTrue(form_json["item_20_indication_or_function"]["semantic_issues"])
        # OCR 模糊原文只能提示核对，不能扩写成确定的固定声明。
        self.assertEqual(form_json["item_24_patent_info"]["value"], "起 利 权 属 志明, 我 们 声明 : 本 中 请 对 他 人 专 利 不 构成 侵 权 .")
        self.assertTrue(form_json["item_24_patent_info"]["semantic_issues"])
        self.assertEqual(form_json["item_25_data_protection"]["value"], "涉及数据保护：是")
        self.assertEqual(form_json["item_28_change_related_items"]["value"], "首次 申请")
        # 没有几何/单元格证据证明只是空表头，不能因缺少固定格式编号
        # 清除残缺OCR文字。完整纯表头的清理另由独立用例验证。
        self.assertEqual(form_json["item_29_other_related_info"]["value"], fake_result["items"][-1]["raw_text"])
        self.assertEqual(form_json["item_29_other_related_info"]["source_text"], fake_result["items"][-1]["raw_text"])

    def test_empty_located_fields_do_not_trigger_neighbor_region_ocr(self) -> None:
        # T2 字段为空且只有标题坐标时，不能扩大区域去邻字段借值。
        service = FilingChangeFormParserService()
        fake_result = {"raw_text": "", "items": [
            {"item_no": n, "raw_text": "", "subfields": {}, "tables": [],
             "source_regions": [{"page": 2, "bbox_pdf": [60, 430, 370, 458]}]}
            for n in (14, 16, 21, 23, 30)
        ]}
        with patch("agent.agent_backend.services.filing_change_form_parser_service.parse_drug_supplement_pdf", return_value=fake_result), patch(
            "agent.agent_backend.services.filing_change_form_parser_service.ocr_pdf_region"
        ) as ocr:
            result = service._parse_pdf_form("unused.pdf")
        ocr.assert_not_called()
        form = result["form_json"]
        self.assertEqual(form["item_14_packaging"]["sub_fields"]["primary_packaging_material"], "")
        self.assertEqual(form["item_16_prescription"]["sub_fields"]["active_ingredients"], "")
        self.assertEqual(form["item_21_change_content"]["value"], "")
        self.assertEqual(form["item_23_original_approval_info"]["value"], "")
        self.assertEqual(form["item_30_applicant_info"]["sub_fields"]["applicant_name"], "")


if __name__ == "__main__":
    unittest.main()
