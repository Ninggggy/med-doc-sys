"""包内真实PDF暴露的两类错误：后识别覆盖及活性成分区域不准确。"""
import unittest
from unittest.mock import patch
from agent.agent_backend.services.filing_change_form_parser_service import FilingChangeFormParserService

OCR = 'agent.agent_backend.services.filing_change_form_parser_service.ocr_pdf_region'


class OCRCandidateTests(unittest.TestCase):
    def setUp(self):
        self.parser = FilingChangeFormParserService()
        self.region = {'page': 2, 'bbox_pdf': [0, 10, 200, 60]}
        self.label = '统一社会信用代码/组织机构代码'

    def credit(self, codes):
        item, fields = {}, {self.label: codes[0]}
        candidates = [{'value': code, 'source_text': str(i)+':'+code, 'source_regions': [self.region]} for i,code in enumerate(codes)]
        self.parser._resolve_credit_ocr(item, fields, self.label, candidates, 31)
        return item,fields

    def test_narrow_invalid_code_does_not_replace_correct_context(self):
        for codes in [('91330701MA2EA5925G','91330701MA2EAS925G'),('91330701MA2EAS925G','91330701MA2EA5925G')]:
            item,fields = self.credit(codes)
            self.assertEqual(fields[self.label],'91330701MA2EA5925G')
            self.assertIn(fields[self.label],item['subfield_sources'][self.label]['source_text'])
            self.assertEqual(len(item['credit_ocr_candidates']),2)
            self.assertNotIn('refinement_issues',item)

    def test_only_invalid_code_is_not_corrected_by_guessing(self):
        item,fields = self.credit(['91330701MA2EAS925G']*2)
        self.assertEqual(fields[self.label],'')
        self.assertEqual(item['refinement_issues'][0]['path'],'sub_fields.credit_or_org_code')
        item.update(item_no=31, subfields=fields, raw_text=self.label+'：91330701MA2EAS925G',
                    recognition_evidence=[{'source_text':'91330701MA2EAS925G','source_regions':[self.region]}])
        schema=self.parser._build_full_form('')
        self.parser._apply_pdf_items_to_schema(schema,[item])
        self.parser._finalize_fields(schema)
        self.assertEqual(schema['item_31_manufacturer_info']['sub_fields']['credit_or_org_code'],'')
        self.assertTrue(schema['item_31_manufacturer_info']['semantic_issues'])

    def test_different_permitted_codes_remain_unconfirmed(self):
        item,fields = self.credit(['91330701MA2EA5925G','91330701MA2EA5926G'])
        self.assertEqual(fields[self.label],'')
        self.assertEqual(len(item['refinement_issues'][0]['candidates']),2)

    def active(self, reads, continuation=False):
        item = {'item_no':16,'raw_text':'活性成分/中药成分/...：误识别成分','source_regions':[self.region]}
        fields = {'活性成分/中药成分/...':'误识别成分','辅料':'辅料值','是否有变更':'否'}
        lines = [{'text':'16. 处方：活性成分/中药成分/...：成分甲','bbox':[0,10,200,25]}]
        if continuation:lines.append({'text':'成分乙','bbox':[100,25,180,38]})
        boundary = 40 if continuation else 22
        lines.append({'text':'辅料：辅料值','bbox':[100,boundary,200,boundary+15]})
        evidence=[]
        with patch(OCR,side_effect=reads) as mock:
            handled=self.parser._refine_active_ingredients('source.pdf',item,lines,self.region,fields,evidence,item['raw_text'])
        self.assertTrue(handled)
        item.update(subfields=fields,recognition_evidence=evidence)
        return item,mock

    def test_active_row_excludes_next_field_and_keeps_exact_source(self):
        item,mock = self.active(['16. 处方：活性成分/中药成分/...：成分甲']*2)
        self.assertEqual(item['subfields']['活性成分/中药成分/...'],'成分甲')
        for call in mock.call_args_list:
            self.assertEqual(call.args[1]['bbox_pdf'],[0,10,200,22])
            self.assertEqual(call.kwargs['psm'],7)
        source=item['subfield_sources']['活性成分/中药成分/...']
        self.assertIn('成分甲',source['source_text'])
        self.assertNotIn('误识别',source['source_text'])

    def test_multiline_ingredients_keep_continuation(self):
        item,mock=self.active(['活性成分/中药成分/...：成分甲\n成分乙']*2,continuation=True)
        self.assertIn('成分乙',item['subfields']['活性成分/中药成分/...'])
        self.assertEqual(mock.call_args.kwargs['psm'],6)
        self.assertEqual(mock.call_args.args[1]['bbox_pdf'][3],38)

    def test_active_conflict_is_not_erased_by_confirmed_no_change(self):
        item,_=self.active(['活性成分/中药成分/...：成分甲','活性成分/中药成分/...：成分乙'])
        schema=self.parser._build_full_form('')
        self.parser._apply_pdf_items_to_schema(schema,[item])
        self.parser._finalize_fields(schema)
        field=schema['item_16_prescription']
        self.assertEqual(field['sub_fields']['has_change'],'否')
        self.assertEqual(field['sub_fields']['active_ingredients'],'')
        self.assertEqual(field['semantic_issues'][0]['path'],'sub_fields.active_ingredients')
        self.assertEqual(len(field['semantic_issues'][0]['candidates']),3)

    def test_failed_local_read_does_not_confirm_old_ocr(self):
        item,_=self.active(['','活性成分/中药成分/...：成分甲'])
        self.assertEqual(item['subfields']['活性成分/中药成分/...'],'')
        self.assertTrue(item['refinement_issues'])


if __name__ == '__main__':
    unittest.main()
