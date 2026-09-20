"""9月17日审计反例：测试真实服务，不能用裸数比较伪造单位正确。"""
import importlib.util
import json
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / "agent_backend/services/filing_change_stability_service.py"
spec = importlib.util.spec_from_file_location("audit_stability", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class StabilityLimitAuditTests(unittest.TestCase):
    def setUp(self):
        self.service = module.FilingChangeStabilityService()

    def test_numeric_decisions(self):
        cases = [
            ("0.5 mg/ml", "≤1 ug/ml", False),
            ("0.5 mg/L", "≤1 mg/g", None),
            ("1 widgets", "≤2 widgets", None),
            ("1", "≤2mg/g", None),
            ("<0.1 或 >0.5", "≤0.2", None),
            ("0.1", "≤0.2 或 ≥1", None),
            ("0.1", "≤0.2（另行规定）", None),
            ("0.3 mg/ml", "≤300 ug/ml", True),
            ("0.3 mg/ml", "<300 ug/ml", False),
            ("-1", "-2～0", True),
            ("-1", "0～-2", None),
            ("9.9e-2", "<0.10", True),
            ("<10cfu/ml", "需氧菌总数≤100cfu/ml", True),
            ("<0.2", "<0.2", True),
            ("≤0.2", "<0.2", None),
            (">0.2", "≤0.2", False),
            ("<LOQ", "≤0.2", None),
            ("未检出", "≤0.2", None),
            ("未检出", "不得检出", True),
            ("不符合规定", "符合规定", False),
            ("符合规定但部分不合格", "符合规定", None),
            ("1,2", "≤20", None),
            ("０．３ｍｇ／ｍＬ", "≤３００μｇ／ｍＬ", True),
            ("90%", "90%～110%", True),
        ]
        for result, limit, expected in cases:
            with self.subTest(result=result, limit=limit):
                decision = self.service._evaluate_limit(result, limit)
                self.assertIs(decision["within_standard"], expected)
                json.dumps(decision)  # Decimal不得泄漏到持久化/API字段。

    def test_composite_duplicates_and_matching(self):
        self.assertIsNone(self.service._judge_within_standard("A:2;A:0", "A:≤1;A:≤1"))
        self.assertFalse(self.service._judge_within_standard("A:2;B:0", "A:≤1;B:≤1"))
        self.assertIsNone(self.service._judge_within_standard("A:0;C:0", "A:≤1;B:≤1"))

    def test_near_limit_uses_same_units_and_meaning(self):
        for limit in ("≤0.2%", "不得过0.2%", "不大于0.2%", "<0.2%"):
            with self.subTest(limit=limit):
                row = self.service._enrich_record({"result_text": "0.18%", "standard_text": limit})
                self.assertTrue(self.service._is_near_limit(row))
        for value, limit, expected in (("180ug/ml", "≤0.2mg/ml", True),
                                        ("210ug/ml", "≤0.2mg/ml", False),
                                        ("103.5%", "不得少于90%", True),
                                        ("103.6%", "≥90%", False),
                                        ("0", "≤0", False), ("90%", "90%～110%", False)):
            with self.subTest(value=value, limit=limit):
                self.assertEqual(self.service._evaluate_limit(value, limit).get("near_limit", False), expected)

    def test_unit_provenance(self):
        row = self.service._enrich_record({"indicator": "含量（%）", "result_text": "90", "standard_text": "≥90%"})
        self.assertTrue(row["within_standard"])
        self.assertEqual(row["result_unit_source"], "indicator")
        row = self.service._enrich_record({"indicator": "浓度(mg/ml)", "result_text": "0.1", "standard_text": "≤0.2mg/g"})
        self.assertIsNone(row["within_standard"])
        self.assertIn("未标注单位", self.service._evaluate_limit("1", "≤2")["unit_note"])
        self.assertEqual(self.service._evaluate_limit('1','≤2')['unit_status'],'unmarked')
        dimensionless=self.service._evaluate_limit('1','≤2',unit_context='无量纲',unit_source='indicator')
        self.assertTrue(dimensionless['within_standard'])
        self.assertEqual(dimensionless['unit_status'],'dimensionless')


if __name__ == "__main__":
    unittest.main()
