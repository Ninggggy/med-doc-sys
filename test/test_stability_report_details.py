import importlib.util
from pathlib import Path
import unittest
import tempfile
import re
import time

spec = importlib.util.spec_from_file_location('report_current', Path(__file__).resolve().parents[1] / 'agent_backend/services/filing_change_report_service.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class StabilityReportDetailTests(unittest.TestCase):
    def test_large_reports_keep_each_saved_exception(self):
        self.assertIsNotNone(module.Document)
        for size in (100, 1000):
            with self.subTest(size=size), tempfile.TemporaryDirectory(prefix='full-exception-report-') as root:
                payload = {'stability_trend_analysis': {'limit_check': {
                    'out_of_spec_count': size,
                    'out_of_spec_details': [{'indicator': f'超限[{i:04d}]', 'result_text': '2',
                        'limit_text': '≤1', 'reason_text': '明确超限', 'source_file_name': f'原件{i}.pdf'} for i in range(size)],
                    'undecidable_count': size,
                    'undecidable_details': [{'indicator': f'待核[{i:04d}]', 'reason_text': '缺少可判断标准'} for i in range(size)],
                }}}
                start = time.monotonic()
                generated = module.FilingChangeReportService(Path(root)).generate_report('synthetic', 'run', payload)
                text = '\n'.join(p.text for p in module.Document(generated['word_path']).paragraphs)
                for body in (text, Path(generated['markdown_path']).read_text()):
                    for kind in ('超限', '待核'):
                        ids = re.findall(kind + r'\[(\d{4})\]', body)
                        self.assertEqual(len(ids), size)
                        self.assertEqual(set(ids), {f'{i:04d}' for i in range(size)})
                    self.assertIn(f'原件{size-1}.pdf', body)
                    self.assertNotIn('历史明细不完整', body)
                print(f'report_exceptions_per_kind={size} generation_and_read_seconds={time.monotonic()-start:.3f}', flush=True)

    def test_all_details_and_component_limitations_are_exported(self):
        details = [{'indicator': f'合成指标{i}', 'result_text': 'A:2', 'limit_text': 'A:≤1;C:≤1',
                    'components': [{'item': 'A', 'result': '2', 'limit': '≤1', 'within_standard': False},
                                   {'item': 'C', 'within_standard': None, 'reason_text': '缺少该组成项检测结果'}]} for i in range(25)]
        payload = {'stability_trend_analysis': {'limit_check': {'out_of_spec_count': 25, 'out_of_spec_details': details,
                    'undecidable_count': 25, 'undecidable_details': [{'indicator': f'待核项{i}'} for i in range(25)]}}}
        service = module.FilingChangeReportService(Path('/unused'))
        text = service._build_markdown('project', 'run', payload, '2026-09-18')
        for index in range(25):
            self.assertIn(f'合成指标{index}', text)
            self.assertIn(f'待核项{index}', text)
        self.assertIn('组成项 A：已超限', text)
        self.assertIn('组成项 C：待确认', text)
        self.assertIn('缺少该组成项检测结果', text)

    def test_historical_truncation_is_explicit(self):
        payload = {'stability_trend_analysis': {'limit_check': {'out_of_spec_count': 25, 'out_of_spec_details': [{}] * 20}}}
        text = module.FilingChangeReportService(Path('/unused'))._build_markdown('p', 'r', payload, 'now')
        self.assertIn('历史明细不完整', text)

    def test_word_file_contains_last_exception_and_missing_component(self):
        self.assertIsNotNone(module.Document, '验收环境必须提供真实Word导出依赖')
        payload = {'stability_trend_analysis': {'limit_check': {'out_of_spec_count': 25,
            'out_of_spec_details': [{'indicator': f'检测项目{i}', 'components': [
                {'item': '缺项C', 'within_standard': None, 'reason_text': '缺少该组成项检测结果'}]} for i in range(25)]}}}
        with tempfile.TemporaryDirectory() as root:
            result = module.FilingChangeReportService(Path(root)).generate_report('synthetic', 'run', payload)
            doc = module.Document(result['word_path'])
            text = '\n'.join(p.text for p in doc.paragraphs)
            self.assertIn('检测项目24', text)
            self.assertIn('缺少该组成项检测结果', text)
