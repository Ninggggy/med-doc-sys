"""真实原件上传存储 + 已保存真实OCR结果重放；不声称再次执行OCR或全文通过。"""
import json
import os
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock
import test_filing_change_review_service_regression as fixture


@unittest.skipUnless(os.getenv('CERTIFICATE_RUN') and os.getenv('CERTIFICATE_INPUT'),'requires local original OCR evidence')
class CertificateRoundtripTests(unittest.TestCase):
    setUp=fixture.FilingChangeReviewServiceRegressionTest.setUp
    tearDown=fixture.FilingChangeReviewServiceRegressionTest.tearDown
    _add_project=fixture.FilingChangeReviewServiceRegressionTest._add_project

    def test_three_shared_read_paths_keep_words_tables_and_blocking(self):
        from agent.agent_backend.services.filing_change_form_parser_service import FilingChangeFormParserService
        from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import build_pdf_form_from_pages
        self._add_project()
        source=sorted(Path(os.environ['CERTIFICATE_INPUT']).glob('*.pdf'))
        self.assertEqual(len(source),3)
        run=Path(os.environ['CERTIFICATE_RUN'])
        self.assertEqual(json.loads((run/'run-complete.json').read_text())['pages'],13)
        for index,kind in enumerate(('submission','reference','application_form'),1):
            pages=json.loads((run/f'doc{index}-pages.json').read_text())
            content=source[index-1].read_bytes()
            upload=fixture._Upload(f'source-{index}.pdf',content)
            if kind=='submission':
                ok,msg,out=self.service.upload_submission_files('project-main',[upload],'5')
                self.assertTrue(ok,msg);doc_id=out['created'][0]['doc_id']
                self.service.material_service=fixture._MaterialParser(deepcopy(pages))
                ok,msg,_=self.service.parse_submission('project-main',doc_id)
            elif kind=='reference':
                ok,msg,out=self.service.upload_reference_materials([upload],'guideline','extend_validity_period',{'drug_category':'化学药品'})
                self.assertTrue(ok,msg);doc_id=out['created'][0]['doc_id']
                self.service.material_service=fixture._MaterialParser(deepcopy(pages))
                ok,msg,_=self.service.parse_reference_material(doc_id)
            else:
                self.service.form_parser=Mock()
                self.service.form_parser.parse_form_file.return_value=FilingChangeFormParserService().form_from_pdf_result(build_pdf_form_from_pages(deepcopy(pages)))
                ok,msg,out=self.service.import_application_form('project-main',upload)
                doc_id=out['original_file_id'] if ok else ''
            self.assertTrue(ok,msg)
            for _ in range(2):
                ok,msg,view=self.service.get_parse_review('project-main',kind,doc_id)
                self.assertTrue(ok,msg)
                saved=view['original_chunks']
                self.assertEqual(len(saved),len(pages))
                for old,new in zip(pages,saved):
                    self.assertTrue(old['words']==new['words'],'存储改变原始词或尺寸采用依据')
                    self.assertEqual(len(old['tables']),len(new['tables']))
                    # 申请字段装配会增加nearest_item_no；原解析字段逐项保持不变。
                    self.assertTrue(all(all(value==stored.get(key) for key,value in original.items())
                        for original,stored in zip(old['tables'],new['tables'])),'存储改变结构化单元格或原表证据')
                    self.assertTrue(old['text']==new['text'],'存储改变正文')
                    self.assertTrue(old.get('ocr_recovery',[])==new.get('ocr_recovery',[]),'存储丢失补识别证据')
                self.assertTrue(any(not issue['resolved'] for issue in view['issues']),'未解决问题被清除')
            self.assertTrue(any(p.read_bytes()==content for p in self.root_dir.rglob('*.pdf')),'上传原件被改变')
        self.assertFalse(self.service.get_parse_readiness('project-main')['ready'])


if __name__=='__main__':unittest.main()
