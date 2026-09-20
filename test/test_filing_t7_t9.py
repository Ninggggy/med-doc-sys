"""T7–T9可观察结果回归；受控模块结果与真实持久化分开。"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from docx import Document
from agent.agent_backend.services.filing_change_result_service import aggregate_result
from agent.agent_backend.services.filing_change_review_orchestrator import FilingChangeReviewOrchestrator
from agent.agent_backend.services.filing_change_evidence_service import FilingChangeEvidenceService
from agent.agent_backend.services.filing_change_report_service import FilingChangeReportService
from agent.test.test_filing_change_review_service_regression import FilingChangeReviewServiceRegressionTest


def inputs():
    fact = {'raw_value': '99%', 'source': {'doc_id': 'a', 'file_name': '质量.docx', 'table': 3, 'row': 4, 'cell': 2}}
    review = {'formal_review': {'result': '通过', 'missing_materials': [], 'content_missing': [], 'parse_issues': []},
              'consistency_check': {'comparisons': [{'field': '含量', 'status': '一致', 'reason': '两端相同', 'left': fact, 'right': fact}]},
              'rule_results': [{'rule_code': 'R1', 'execution_status': 'completed', 'status': '通过',
                                'subchecks': [{'name': '限度', 'status': '通过', 'reason': '99%符合标准', 'evidence': [fact]}]}], 'matched_rules': []}
    tech = {'quality_standard_check': {'result': '基本符合', 'items': [fact]},
            'stability_trend_analysis': {'result': '支持延长', 'records': [fact]},
            'change_category_suggestion': {'suggested_category': '备案类', 'reason': '申请事项1.7'}, 'change_identification': {}}
    return review, tech, [{'rule_code': 'R1'}]


class SummaryTests(unittest.TestCase):
    def test_sufficient_checks_and_empty_or_missing_never_pass(self):
        r,t,rules=inputs();self.assertFalse(aggregate_result(r,t,rules)['overall_conclusion']['need_manual_review'])
        for module in ('quality_standard_check','stability_trend_analysis'):
            broken=copy.deepcopy(t);broken[module]={}
            self.assertTrue(aggregate_result(r,broken,rules)['overall_conclusion']['need_manual_review'])
        self.assertTrue(aggregate_result({}, {}, [])['overall_conclusion']['need_manual_review'])
        self.assertTrue(all(x is None for x in aggregate_result({}, {}, [])['issue_counts'].values()))
        no_evidence=copy.deepcopy(r);no_evidence['consistency_check']['comparisons'][0].pop('right')
        self.assertTrue(aggregate_result(no_evidence,t,rules)['overall_conclusion']['need_manual_review'])
        r['rule_results']=[];self.assertIn('未执行',aggregate_result(r,t,rules)['overall_conclusion']['summary'])

    def test_quality_insufficient_stability_manual_and_confirmed_problem_coexist(self):
        r,t,rules=inputs();t['quality_standard_check']={'result':'需人工确认','reason':'缺少标准版本数据'}
        t['stability_trend_analysis']={'result':'需人工确认','summary':'定性检测需人工确认'}
        r['rule_results'][0]['subchecks'] += [{'name':'限度问题','status':'发现问题','reason':'B2 12月含量94%','evidence':[{'batch':'B2'}]}]
        r['formal_review']['parse_issues']=[{'file_name':'报告.pdf','message':'第2页解析失败'}]
        result=aggregate_result(r,t,rules)
        self.assertEqual({x['status'] for x in result['issues']}, {'证据不足','发现问题','解析异常'})
        for text in ('缺少标准版本数据','定性检测需人工确认','94%','解析失败'):self.assertIn(text,result['overall_conclusion']['summary'])
        self.assertEqual(t['change_category_suggestion']['suggested_category'],'备案类')
        self.assertTrue(all('未提交' not in i['required_action'] for i in result['issues'] if i['kind']=='parse_error'))
        self.assertTrue(any(i['kind']=='technical_issue' and '94%' in i['reason'] for i in result['issues']))
        self.assertFalse(any('94%' in i['reason'] for i in result['issue_details']['content_missing']))
        r['rule_results'][0]['rule_code']='TVS-CONS-001'
        result=aggregate_result(r,t,rules)
        self.assertTrue(any('94%' in i['reason'] for i in result['issue_details']['conflict']))

    def test_not_applicable_retains_reason_and_execution_failure(self):
        r,t,rules=inputs();r['rule_results'][0]['subchecks']=[{'name':'参比','status':'不适用','reason':'本项目未使用参比'}]
        self.assertFalse(aggregate_result(r,t,rules)['issues'])
        r['rule_results'][0]['execution_status']='failed'
        result=aggregate_result(r,t,rules);self.assertTrue(any(i['status']=='执行失败' for i in result['issues']))

    def test_model_failure_missing_fields_invented_refs_and_changed_values_fall_back(self):
        r,t,rules=inputs();t['quality_standard_check']={'result':'需人工确认','reason':'缺标准版本数据'}
        for response in [RuntimeError('PRIVATE_MODEL_RESPONSE'), {}, {'summary':'全部符合'}, {'explanation_order':[0], 'evidence_refs':['fake']}, {'explanation_order':[0], 'value':'100%'}, {'explanation_order':[999]}]:
            o=FilingChangeReviewOrchestrator(Mock(),Mock(),Mock())
            o.form_parser.normalize_form_json=lambda x:x;o.extraction.run=Mock(return_value={})
            o.technical.run=Mock(return_value=copy.deepcopy(t));o.rule_review.run=Mock(return_value=copy.deepcopy(r))
            o.llm_gate.should_call_llm=lambda **kw:{'allow':True}
            o.llm_reasoning.summarize_overall=Mock(side_effect=response) if isinstance(response,Exception) else Mock(return_value=response)
            result=o.run('p',{'form_json':{}},[],rules,[],{}, {})
            self.assertEqual(result['llm_calls'][0]['status'],'fallback')
            self.assertNotIn('PRIVATE_MODEL_RESPONSE', json.dumps(result, ensure_ascii=False))
            self.assertIn('原始事实顺序', result['llm_calls'][0]['reason'])
            self.assertIn('原始事实顺序', result['review_report_markdown'])
            exported = FilingChangeReportService(Path('/unused'))._build_markdown('p', 'r', result, 'now')
            self.assertIn('原始事实顺序', exported)
            self.assertNotIn('PRIVATE_MODEL_RESPONSE', exported)
            self.assertIn('缺标准版本数据',result['overall_conclusion']['summary'])
            self.assertEqual(result['conclusion']['ai_judgement'],result['review_report_draft']['ai_preliminary_conclusion'])
            self.assertIn(result['overall_conclusion']['result'],result['review_report_markdown'])

    def test_actual_fact_locations_and_form_values_not_first_chunk(self):
        r,t,_=inputs();r['consistency_check']['comparisons'][0]['right']={'raw_value':'98%','source':{'doc_id':'b','file_name':'证明.pdf','page':7,'table':2,'row':8,'cell':4}}
        service=FilingChangeEvidenceService()
        ev=service.collect([{'doc_id':'a','file_name':'质量.docx'},{'doc_id':'b','file_name':'证明.pdf'}],[],[],{'a':[{'text':'无关封面'}]}, {'item_5_application_matter_category':{'selected_values':['1.7']}}, r)
        refs=ev['evidence_refs'];self.assertTrue(any('页 7' in e['position'] and e['snippet']=='98%' for e in refs))
        self.assertTrue(any('表 3' in e['position'] and '行 4' in e['position'] and '页' not in e['position'] for e in refs))
        self.assertTrue(any(e['source_type']=='application_form' and e['snippet']=="['1.7']" for e in refs))
        self.assertNotIn('无关封面',json.dumps(refs,ensure_ascii=False))

    def test_all_19_rules_with_explicit_controlled_evidence_support_summary(self):
        from agent.test.test_filing_t4_t6 import good_context
        from agent.agent_backend.services.filing_change_rule_review_service import FilingChangeRuleReviewService, definitions
        from agent.agent_backend.services.filing_change_quality_check_service import FilingChangeQualityCheckService
        c=good_context();rules=list(definitions().values());st=c['stability']
        # 汇总验收用的受控模块输出，明确附上方案/逐组记录与显著变化证据；不冒充真实模型结论。
        st['result']='支持延长';st['coverage']['requirements_evidence']=[f for f in c['facts'] if f['field'].startswith('planned_')]
        st['coverage']['groups']=copy.deepcopy(st['records'])
        st['significant_change_assessment']['assessments']=copy.deepcopy(st['records'])
        q=FilingChangeQualityCheckService().run(c['submissions'])
        r=FilingChangeRuleReviewService().run({'application_form':c['form'],'facts':c['facts']},c['submissions'],rules,c['completeness'],st,q)
        result=aggregate_result(r,{'quality_standard_check':q,'stability_trend_analysis':st},rules)
        self.assertEqual(len(r['rule_results']),19);self.assertFalse(result['issues'],result['issues'])
        self.assertEqual(result['overall_conclusion']['result'],'AI 初步建议：可继续审评')

    def test_unresolved_category_and_unknown_rule_status_need_action(self):
        r,t,rules=inputs();t['change_identification']={'need_manual_review':True};t['change_category_suggestion']={'reason':'事项选择不明确','need_manual_review':True}
        result=aggregate_result(r,t,rules);self.assertTrue(result['overall_conclusion']['need_manual_review']);self.assertIn('事项选择不明确',result['overall_conclusion']['summary'])
        r,t,rules=inputs();r['rule_results'][0]['subchecks'][0]['status']='未识别状态'
        result=aggregate_result(r,t,rules);self.assertIn('补充',result['recommended_action'])

    def test_word_full_table_and_unknown_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            records=[{'batch_no':'B'+str(i),'result_text':str(i),'indicator':'含量','source_caption':'表3','source_row_index':i} for i in range(120)]
            p={'stability_trend_analysis':{'records':records},'manual_confirmation':{'comment':'复核保留120行'},'overall_conclusion':{'result':'需人工确认'}}
            result=FilingChangeReportService(Path(tmp)).generate_report('p','run1',p)
            doc=Document(result['word_path']);self.assertEqual(len(doc.tables[0].rows),121)
            self.assertIn('B119',doc.tables[0].rows[-1].cells[0].text)
            self.assertIn('审评完成时间（北京时间）：历史结果未记录',result['markdown'])
            self.assertIn('复核保留120行',result['markdown'])
            p['issue_counts'] = {'parse_error': 0, 'conflict': None}
            result=FilingChangeReportService(Path(tmp)).generate_report('p','run1',p)
            self.assertIn('解析异常：0', result['markdown'])
            self.assertIn('信息冲突：历史结果未记录', result['markdown'])
            self.assertIn('解析异常：0', '\n'.join(x.text for x in Document(result['word_path']).paragraphs))


class PersistenceTests(FilingChangeReviewServiceRegressionTest):
    # 只运行下面两项；继承的既有服务用例由独立回归运行。
    def test_t789_manual_stale_save_and_regeneration_keep_round(self):
        self._add_project('p');self._add_run_and_result('p','old');self._add_run_and_result('p','new')
        self.service.report_service=FilingChangeReportService(self.root_dir)
        ok,msg,saved=self.service.manual_confirm_run('old',{'comment':'旧轮人工意见','expected_revision':0});self.assertTrue(ok,msg)
        ok,msg,_=self.service.manual_confirm_run('old',{'comment':'覆盖','expected_revision':0});self.assertFalse(ok);self.assertIn('刷新',msg)
        ok,msg,r=self.service.generate_report('old');self.assertTrue(ok,msg);self.assertEqual(r['run_id'],'old');self.assertIn('旧轮人工意见',r['report_content'])
        self.assertFalse(self.service.get_run_result('new')[2]['manual_confirmation'])
        from agent.test.test_filing_change_review_service_regression import _TestConnection
        from agent.agent_backend.services.filing_change_review_service import FilingChangeReviewService
        reader=object.__new__(FilingChangeReviewService);reader.db_conn=_TestConnection(self.base_dir/'regression.sqlite')
        self.assertEqual(reader.get_run_result('old')[2]['manual_confirmation']['comment'],'旧轮人工意见');reader.db_conn.engine.dispose()

    def test_t789_export_after_manual_save_and_explicit_report_read(self):
        self._add_project('p');self._add_run_and_result('p','old');self._add_run_and_result('p','new')
        self.service.report_service=FilingChangeReportService(self.root_dir)
        ok,msg,report=self.service.generate_report('old');self.assertTrue(ok,msg)
        self.assertTrue(self.service.manual_confirm_run('old',{'comment':'导出必须包含最新人工意见','expected_revision':0})[0])
        ok,msg,export=self.service.export_report_word(report['report_id']);self.assertTrue(ok,msg)
        doc=Document(export['file_path']);self.assertIn('导出必须包含最新人工意见','\n'.join(p.text for p in doc.paragraphs))
        self.service.generate_report('new')
        ok,msg,old=self.service.get_project_latest_report('p',run_id='old');self.assertTrue(ok,msg);self.assertEqual(old['run_id'],'old')
        self.assertFalse(self.service.get_project_latest_report('p',run_id='other')[0])
        payload=self.service.get_run_result('old')[2]
        self.assertIn(payload['report_generated_at'],old['report_content'])

    def test_t789_legacy_read_does_not_write_or_invent_counts(self):
        self._add_project('p');self._add_run_and_result('p','old')
        result=self.service.get_run_result('old')[2]
        self.assertTrue(all(x is None for x in result['issue_counts'].values()))
        self.assertEqual(result['technical_support']['status'],'历史结果未记录')


if __name__=='__main__':
    l=unittest.TestLoader();suite=l.loadTestsFromTestCase(SummaryTests)
    for n in ('test_t789_manual_stale_save_and_regeneration_keep_round','test_t789_legacy_read_does_not_write_or_invent_counts','test_t789_export_after_manual_save_and_explicit_report_read'):suite.addTest(PersistenceTests(n))
    r=unittest.TextTestRunner(verbosity=2).run(suite);raise SystemExit(not r.wasSuccessful())
