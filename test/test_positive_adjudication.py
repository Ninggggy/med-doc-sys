"""正向采用与误关联反例；图像识别成绩另存，不用这些机制样例代替。"""
import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ocr_service/paddle_runtime'))
from seal_review import adopt_verified_seals, reconcile_seal_issues, attach_authority_values
from sequence_adjudication import local_competitions


class PositiveAdjudication(unittest.TestCase):
    def decision(self):
        return dict(accepted=True,seal_index=0,curve_index=0,text='示例登记机关',
                    polygon_page=[[90,20],[130,20],[130,60],[90,60]],
                    seal_polygon_page=[[85,15],[135,15],[135,65],[85,65]],
                    integrity={'eligible':True,'rectangle_iou':.55},
                    covered_sources=[dict(source_span_id='seal-fragment',text='机',red_ink_coverage=1,
                                          red_ink_fraction=1,neutral_dark_fraction=0)])

    def test_full_curve_replaces_only_verified_source_and_preserves_evidence(self):
        lines=[dict(text='地址正文',source_span_id='body',bbox=[0,0,50,10]),
               dict(text='机',source_span_id='seal-fragment',bbox=[90,20,100,40])]
        result,decisions=adopt_verified_seals(lines,{'seal_adjudications':[self.decision()]})
        self.assertEqual([l['text'] for l in result],['地址正文','示例登记机关'])
        self.assertEqual(result[1]['original_source_lines'][0]['text'],'机')
        self.assertEqual(lines[1]['text'],'机')
        self.assertEqual(decisions[0]['applied_source_span_ids'],['seal-fragment'])

    def test_changed_effective_source_cannot_be_overwritten(self):
        line=dict(text='人工修改',source_span_id='seal-fragment',human_revision=True)
        result,decisions=adopt_verified_seals([line],{'seal_adjudications':[self.decision()]})
        self.assertEqual(result,[line]);self.assertEqual(decisions,[])

    def test_seal_resolution_cannot_clear_body_or_whole_cell(self):
        decision=self.decision();decision['applied_source_span_ids']=['seal-fragment']
        issues=[{'code':'ocr_quality','source_span_id':'seal-fragment','text':'机'},
                {'code':'ocr_quality','source_span_id':'body','text':'住所'},
                {'code':'ocr_overlay_conflict','source_span_id':'seal-fragment','text':'机'}]
        observations=[];remaining=reconcile_seal_issues({'applied_seal_adjudications':[decision]},issues,observations)
        self.assertEqual(remaining,issues[1:]);self.assertEqual(len(observations),1)
        self.assertEqual(observations[0]['original_issue'],issues[0])

    def test_same_id_with_different_text_stays_unresolved(self):
        decision=self.decision();decision['applied_source_span_ids']=['seal-fragment']
        issues=[{'code':'ocr_quality','source_span_id':'seal-fragment','text':'正文'}]
        self.assertEqual(reconcile_seal_issues({'applied_seal_adjudications':[decision]},issues,[]),issues)

    def view(self):
        decision=self.decision();lines,applied=adopt_verified_seals([],{'seal_adjudications':[decision]})
        field=dict(region_id='field',label='登记机关',value='',text='登记机关：',bbox_pdf=[20,25,80,45],
                   source_span_ids=['label'],source_lines=[dict(source_span_id='label',text='登记机关：',polygon=[[20,25],[80,25],[80,45],[20,45]])])
        return dict(lines=lines,applied_seal_adjudications=applied,readable_elements=[field],
                    layout_regions=[dict(region_id='seal',label='seal',bbox_pdf=[85,15,135,65])])

    def test_unique_same_seal_populates_authority_with_all_sources(self):
        view=self.view();attach_authority_values(view);field=view['readable_elements'][0]
        self.assertEqual(field['value'],'示例登记机关')
        self.assertEqual(field['source_span_ids'],['label','seal:0:curve:0'])
        self.assertTrue(field['related_seal_region']['value_verified'])

    def test_other_seal_cannot_populate_authority_even_if_neighboring(self):
        view=self.view();view['applied_seal_adjudications'][0]['seal_polygon_page']=[[90,15],[140,15],[140,65],[90,65]]
        attach_authority_values(view);self.assertEqual(view['readable_elements'][0]['value'],'')

    def test_existing_authority_value_is_protected(self):
        view=self.view();view['readable_elements'][0]['value']='既有修订'
        attach_authority_values(view);self.assertEqual(view['readable_elements'][0]['value'],'既有修订')

    def test_competing_authority_seals_stay_unresolved(self):
        view=self.view();view['layout_regions'].append(dict(region_id='second',label='seal',bbox_pdf=[87,15,137,65]))
        attach_authority_values(view);self.assertEqual(view['readable_elements'][0]['value'],'')

    def test_scoring_uses_effective_context_not_auxiliary_unrelated_mistakes(self):
        groups=local_competitions('甲乙正文',['甲丙正文'],'甲丙正丈')
        self.assertEqual({v['text'] for v in groups[0]['variants']},{'甲乙正文','甲丙正文'})

    def test_verified_seal_never_concatenates_with_month_or_body(self):
        from assembly import assemble_chunk
        line=dict(text='示例登记机关',source_span_id='seal',source_span_ids=['seal'],
                  bbox=[10,10,70,40],polygon=[[10,10],[70,10],[70,40],[10,40]],seal_verification=self.decision())
        month=dict(text='月',source_span_id='month',bbox=[30,35,40,45])
        chunk=dict(lines=[line,month],tables=[],layout_regions=[dict(region_id='r',label='seal',order=1,bbox_pdf=[0,0,80,60])])
        assemble_chunk(chunk)
        self.assertEqual([e['text'] for e in chunk['readable_elements']],['月','印章文字：示例登记机关'])
        self.assertNotIn('示例登记机关月',chunk['text'])

    def test_authority_transfer_removes_only_duplicate_seal_element(self):
        view=self.view();line=view['lines'][0]
        view['readable_elements'].append(dict(kind='field',label='印章文字',region_id='seal-text',
                                             source_span_ids=line['source_span_ids'],source_lines=[line],text=line['text']))
        attach_authority_values(view)
        self.assertEqual(len(view['readable_elements']),1)
        self.assertEqual(len(view['seal_evidence_elements']),1)
        self.assertEqual(view['readable_elements'][0]['bbox_pdf'],[20,20,130,60])


if __name__=='__main__':unittest.main()
