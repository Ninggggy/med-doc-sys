"""并发合入后的子字段格式兼容；不改变字段业务角色及旧断言。"""
import unittest

from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import parse_subfields


class SubfieldCompatibilityTests(unittest.TestCase):
    def test_adjacent_explicit_labels(self):
        fields = parse_subfields('中文名称：甲公司联系人：张三注册地址：甲市甲路')
        self.assertEqual(fields['中文名称'], '甲公司')
        self.assertEqual(fields['联系人'], '张三')
        self.assertEqual(fields['注册地址'], '甲市甲路')

    def test_multiline_chinese_and_english_values(self):
        fields = parse_subfields('注册地址：\n甲市\n甲路三号\n英文名称：Example Tablets')
        self.assertEqual(fields['注册地址'], '甲市甲路三号')
        self.assertEqual(fields['英文名称'], 'Example Tablets')

    def test_bare_label_newline(self):
        self.assertEqual(parse_subfields('研究负责人\n张三')['研究负责人'], '张三')

    def test_bare_label_in_value_is_not_a_boundary(self):
        self.assertEqual(parse_subfields('中文名称：负责研究负责人 培训的甲公司'),
                         {'中文名称': '负责研究负责人培训的甲公司'})
