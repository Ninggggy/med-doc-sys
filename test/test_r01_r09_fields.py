"""R01–R09 实际复现形成的最小规则回归；真实文件验收另在 r01_r09_20260907/verify.py。"""
import unittest
from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import item_start, parse_subfields
from agent.agent_backend.utils.parser.form_field_semantics import heading
from agent.agent_backend.services.filing_change_form_parser_service import FilingChangeFormParserService


class OriginalFailures(unittest.TestCase):
    def test_wrapped_numbered_headings_and_body_number(self):
        self.assertEqual(item_start('13. 同 品种 已 被 受理 或 同期 申报 的 其 他 制剂 规格\n:')[0],13)
        self.assertEqual(item_start('17. 原 / 辅 料 / 包 材 来 源\n:')[0],17)
        self.assertEqual(item_start('30. 药品 注册 申请 人\n:')[0],30)
        self.assertIsNone(item_start('6. 已上市化学药品药学变更相关技术指导原则中属于重大变更的事项'))
        self.assertIsNone(item_start('6.8 变更有效期和贮藏条件'))

    def test_word_category_prompt_is_not_value(self):
        self.assertEqual(heading('主要适应症或者功能主治（分类：'),(20,'主要适应症或者功能主治',''))

    def test_blank_description_does_not_borrow_category(self):
        parser=FilingChangeFormParserService();schema=parser._build_full_form('')
        parser._apply_pdf_items_to_schema(schema,[{'item_no':20,'raw_text':'适应症分类：皮肤及五官科药物\n'}])
        parser._sanitize_pdf_field_values(schema)
        self.assertEqual(schema['item_20_indication_or_function']['sub_fields'],{'category':'皮肤及五官科药物','description':''})

    def test_prescription_spaced_labels_and_newline_colon(self):
        result=parse_subfields('活性 成 份 / 中 药 成 份 /..\n: 米 诺 地 尔\n辅料 : 纯化 水\n是否有变更: 否')
        self.assertEqual(result['活性成分/中药成分/...'],'米诺地尔')
        self.assertEqual(result['辅料'],'纯化水')
        self.assertEqual(result['是否有变更'],'否')

    def test_supplier_header_does_not_remove_material_column(self):
        result=FilingChangeFormParserService()._guess_material_source_rows([
            [['原/辅料/包材名称','登记号','受理号','生产企业名称'], ['纯化水','无','/','浙江赛默制药有限公司']]])
        self.assertEqual(result,[{'material_name':'纯化水','register_no':'无','accept_no':'/','manufacturer':'浙江赛默制药有限公司'}])

    def test_party_position_and_empty_license_are_bounded(self):
        fields=parse_subfields('法定代表人: 邵春能 职位: 法定代表人\n注册地址: 浙江\n《药品生产许可证》编号:\nGMP证书 编号 (如有) :')
        self.assertEqual(fields['法定代表人'],'邵春能')
        self.assertEqual(fields['职位'],'法定代表人')
        self.assertEqual(fields['《药品生产许可证》编号'],'')
        self.assertEqual(parse_subfields('《药品生产许可证》编号 浙')['《药品生产许可证》编号'],'浙')

    def test_ocr_residual_yes_cannot_confirm_data_protection(self):
        parser=FilingChangeFormParserService();schema=parser._build_full_form('')
        field=schema['item_25_data_protection']
        field.update(source_text='是 否 涉及 数据 保护 :\n是\n8',value='是',option_fragments=['是\n8'],recognition_evidence=[{'source_text':'是\n8'}])
        parser._finalize_fields(schema)
        self.assertEqual(field['value'],'');self.assertTrue(field['semantic_issues'])

    def test_bad_ocr_dosage_remains_candidate(self):
        parser=FilingChangeFormParserService();field=parser._build_full_form('')['item_11_dosage_form']
        field.update(source_text='中国约典剂型 : 片齐',recognized_text='中国约典剂型 : 片齐',recognition_evidence=[{'source_text':'中国约典剂型 : 片齐'}])
        parser._apply_multiple_choice(field,[field['source_text']])
        self.assertEqual(field['selected_values'],[])
        self.assertEqual(field['selection_status'],'unconfirmed')


if __name__=='__main__':unittest.main()
