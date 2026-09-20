"""OCR词/表格到限度判断的来源保护，可靠值继续参与判断。"""
import unittest
import fitz

from agent.agent_backend.utils.parser.pdf_page_extractor import table_entry, ocr_region
from agent.agent_backend.services.filing_change_stability_service import FilingChangeStabilityService
from agent.agent_backend.services.filing_change_extraction_service import extract_document


def evidence():
    return {'status': 'numeric_uncertain', 'reason_code': 'numeric_candidates_disagree',
            'primary': '1.25', 'secondary': '-1.25', 'bbox_pdf': [20,20,35,30], 'page': 1}


class NumericFlowTests(unittest.TestCase):
    def test_legacy_and_new_service_metadata_are_mapped_to_pdf_coordinates(self):
        class Client:
            def __init__(self, checks): self.checks = checks
            def image_to_data(self, *args, **kwargs):
                payload = {'text':['1.25'], 'left':[40], 'top':[20], 'width':[30], 'height':[10], 'conf':[99]}
                if self.checks is not None: payload['numeric_verification'] = self.checks
                return payload
        with fitz.open() as doc:
            page = doc.new_page(width=100, height=100)
            old, _, _ = ocr_region(page, page.rect, Client(None), 'eng', 144, 3)
            self.assertEqual(old[0]['numeric_verification']['status'], 'numeric_uncertain')
            self.assertEqual(old[0]['numeric_verification']['reason_code'], 'numeric_verification_missing')
            checks = [{**evidence(), 'word_indices':[0], 'bbox_pixel':[30,15,80,36]}]
            new, _, _ = ocr_region(page, page.rect, Client(checks), 'eng', 144, 3)
            self.assertEqual(new[0]['numeric_verification']['bbox_pdf'], [15,7.5,40,18])
            self.assertEqual(new[0]['numeric_verification']['page'], 1)

    def test_pdf_fact_row_and_numeric_evidence_survive_extraction(self):
        table = {'columns':['项目','填写值'], 'rows':[['项目','填写值'],['有效期','24个月'],['药品名称','合成药']],
                 'cells':[{'row':1,'column':1,'text':'24个月','numeric_verification':[evidence()]}]}
        payload = extract_document('synthetic.pdf', [{'page':1,'tables':[table]}])
        validity = [f for f in payload['facts'] if f['field']=='validity_period']
        self.assertTrue(validity)
        self.assertEqual(validity[0]['status'], 'manual_review')
        self.assertEqual(validity[0]['source']['row'], 2)
        self.assertEqual(validity[0]['source']['cell'], 2)
        self.assertEqual(payload['validity_period'], '')
        drug = [f for f in payload['facts'] if f['field']=='drug_name']
        self.assertEqual(drug[0]['status'], 'available')

    def test_table_preserves_candidate_location_and_requires_review(self):
        words = [{'text': '1.25', 'bbox': [20,20,35,30], 'numeric_verification': evidence()}]
        table = table_entry([[10,10,50,40]], words, 1, 1, 'ocr_grid')
        self.assertEqual(table['rows'], [['1.25']])
        self.assertTrue(table['needs_review'])
        self.assertEqual(table['cells'][0]['numeric_status'], 'numeric_uncertain')
        self.assertEqual(table['cells'][0]['numeric_verification'][0]['secondary'], '-1.25')

    def test_uncertain_measurement_does_not_enter_numeric_trend_or_limits(self):
        service = FilingChangeStabilityService()
        uncertain = service._enrich_record({'indicator':'含量', 'time_point':'0月', 'result_text':'1.25',
            'limit_text':'≤1', 'numeric_verification':[evidence()]})
        self.assertIsNone(uncertain['result_num'])
        self.assertIsNone(uncertain['within_standard'])
        self.assertFalse(uncertain['near_limit'])
        self.assertEqual(uncertain['limit_reason'], 'numeric_uncertain')
        detail = service._limit_detail(uncertain)
        self.assertEqual(detail['numeric_verification'][0]['secondary'], '-1.25')
        reliable = service._enrich_record({'indicator':'含量','time_point':'3月','result_text':'2','limit_text':'≤1'})
        self.assertFalse(reliable['within_standard'])
        indicators = service._build_indicator_results({'含量':[uncertain, reliable]})
        self.assertEqual(indicators[0]['risk_level'], '高')

    def test_long_table_maps_evidence_to_affected_row_only(self):
        service = FilingChangeStabilityService()
        table = {'source_table': {'cells':[{'row':1,'column':2,'numeric_verification':[evidence()]}]}}
        records = service._build_long_table_records(table, ['time_point','indicator','result','limit'],
            [['0月','含量','1.25','≤1'], ['3月','含量','2','≤1']])
        first, second = [service._enrich_record(r) for r in records]
        self.assertIsNone(first['within_standard'])
        self.assertFalse(second['within_standard'])

    def test_uncertain_standard_blocks_only_its_wide_column(self):
        service = FilingChangeStabilityService()
        table = {'headers':['时间点','含量','杂质'], 'source_table': {'cells':[
            {'row':1,'column':1,'numeric_verification':[evidence()]}]}}
        records = service._build_wide_table_records(table, ['time_point','含量','杂质'],
            [['限度','≤1','≤1'],['0月','2','2']])
        first, second = [service._enrich_record(r) for r in records]
        self.assertIsNone(first['within_standard'])
        self.assertFalse(second['within_standard'])


if __name__ == '__main__':
    unittest.main()
