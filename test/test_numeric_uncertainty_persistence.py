"""真实上传存储/SQLite/解析结果保存与报告；解析边界使用合成OCR结果。"""
import json
from pathlib import Path
import unittest

import test_filing_change_review_service_regression as fixture
from test_filing_change_review_service_regression import _Upload, _MaterialParser
from test_numeric_uncertainty_flow import evidence
from agent.agent_backend.utils.parser.pdf_page_extractor import table_entry
from agent.agent_backend.services.filing_change_stability_service import FilingChangeStabilityService
from agent.agent_backend.services.filing_change_report_service import FilingChangeReportService


def parsed_page():
    rows = [['时间点','指标','结果','限度'], ['0月','含量','1.25','≤1'], ['3月','含量','2','≤1']]
    cells, words = [], []
    for r, row in enumerate(rows):
        for c, text in enumerate(row):
            box = [c*100, r*30, (c+1)*100, (r+1)*30]
            cells.append(box)
            word = {'text':text, 'bbox':[box[0]+2,box[1]+2,box[2]-2,box[3]-2], 'source':'ocr'}
            if r == 1 and c == 2:
                word['numeric_verification'] = evidence()
            words.append(word)
    table = table_entry(cells, words, 1, 1, 'ocr_grid')
    return {'page':1, 'text':'稳定性研究\n'+table['markdown'], 'tables':[table],
            'tables_in_text':True, 'section_path':['稳定性研究'], 'content_available':True,
            'errors':[{'stage':'ocr_numeric','code':'numeric_uncertain','numeric_verification':[evidence()]}]}


class NumericPersistenceTests(unittest.TestCase):
    setUp = fixture.FilingChangeReviewServiceRegressionTest.setUp
    tearDown = fixture.FilingChangeReviewServiceRegressionTest.tearDown
    _add_project = fixture.FilingChangeReviewServiceRegressionTest._add_project

    def test_saved_partial_parse_remains_uncertain_in_new_analysis_and_report(self):
        pid = 'numeric-persistence'
        self._add_project(pid)
        ok, message, uploaded = self.service.upload_submission_files(pid, [_Upload('稳定性资料.txt', b'synthetic')], '5', '5.6')
        self.assertTrue(ok, message)
        # 通过列表取得真实生成的资料主键，不绕过上传/存储。
        doc_id = self.service.list_submissions(pid, {})['list'][0]['doc_id']
        self.service.material_service = _MaterialParser([parsed_page()])
        ok, message, attempt = self.service.parse_submission(pid, doc_id)
        self.assertTrue(ok, message)
        self.assertEqual(attempt['content_status'], 'partial')
        ok, message, saved = self.service.get_submission_parsed_markdown(pid, doc_id)
        self.assertTrue(ok, message)
        self.assertEqual(saved['parsed_chunks'][0]['tables'][0]['cells'][6]['numeric_status'], 'numeric_uncertain')
        # 从磁盘重新读取manifest，验证不是只靠进程中对象携带证据。
        manifest = self.service._load_submission_manifest(pid)
        meta = manifest['doc_meta'][doc_id]
        submission = {**meta, 'doc_id':doc_id, 'file_name':'稳定性资料.txt'}
        service = FilingChangeStabilityService()
        result = service.run({}, [submission])
        self.assertEqual(result['record_count'], 2)
        self.assertEqual(result['limit_check']['out_of_spec_count'], 1)
        self.assertEqual(result['limit_check']['undecidable_count'], 1)
        uncertain = result['limit_check']['undecidable_details'][0]
        self.assertEqual(uncertain['numeric_verification'][0]['secondary'], '-1.25')
        self.assertEqual(result['key_indicators'][0]['risk_level'], '高')
        round_trip = json.loads(json.dumps(result, ensure_ascii=False))
        report = FilingChangeReportService(self.root_dir).generate_report(pid, 'synthetic-run', {'stability_trend_analysis':round_trip})
        markdown = Path(report['markdown_path']).read_text()
        self.assertIn('-1.25', markdown)
        self.assertIn('numeric_uncertain', markdown)


if __name__ == '__main__':
    unittest.main()
