"""T4–T6业务复验：受控原文、原始Word、完整规则正反例。"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from agent.agent_backend.services.filing_change_extraction_service import extract_document, collect_facts, compare_facts, content_completeness, normalize, COMPARISON_FIELDS, classify_table
from agent.agent_backend.services.filing_change_rule_review_service import FilingChangeRuleReviewService, definitions, validate_rule
from agent.agent_backend.services.filing_change_quality_check_service import bridge_check
from agent.agent_backend.services.filing_change_stability_service import FilingChangeStabilityService
from agent.agent_backend.services.filing_change_review_service import FilingChangeReviewService
from agent.agent_backend.services.filing_change_submission_parser import parse_filing_change_docx

OUT=Path(__file__).parent/'t4_t6_20260906'


def doc(text,id='d',kind=None):
    ex=extract_document(id+'.txt',[{'text':text,'page':1}])
    if kind is not None:ex['document_types']=kind
    return {'doc_id':id,'file_name':id+'.txt','parse_status':'success','extracted_json':ex,'review_enabled':True}


def fact(key,value,id='a',context='current'):
    return {'field':key,'label':key,'raw_value':value,'normalized_value':normalize(value),'status':'available','availability':'current','context':context,'source':{'doc_id':id,'file_name':id+'.txt','page':1,'line':1}}


def record(batch='B1',month=24,condition='30℃',pack='30ml',value='99',item='含量'):
    return {'batch_no':batch,'month':month,'time_point':f'{month}月','orientation':'正置','product_role':'自制制剂','condition':condition,'specification':'1%','packaging':pack,'indicator':item,'result_text':value,'limit_text':'95%~105%','source_doc_id':'stable','source_caption':'检测结果表','source_row_index':1,'source_column_index':2}


def good_context():
    vals={'drug_name':'受控药品','approval_no':'国药准字H12345678','dosage_form':'溶液剂','specification':'1%','packaging':'30ml','fill_volume':'每瓶30ml','holder':'申请企业','manufacturer':'生产企业','validity_period':'24个月','storage_condition':'密封，30℃以下',
          'prescription':'成分甲1g','approved_prescription':'成分甲1g','process':'混合过滤','approved_process':'混合过滤','critical_parameters':'30℃','approved_parameters':'30℃','batch_size':'150kg','commercial_scale':'150kg','batch_sequence':'连续生产B1、B2、B3',
          'standard_no':'S1','test_standard':'S1','effective_date':'2025-01-01','method_validation':'验证通过，试验数据见表1',
          'revision_info':'有效期由18个月修订为24个月','packaging_registration':'Y20250000001','packaging_registration_status':'登记符合要求','compatibility':'试验符合规定，相容','compatibility_data':'S1研究：24月迁移0.01mg，限度≤0.1mg；符合限度','migration_data':'S1：24月0.01mg，限度≤0.1mg','adsorption_data':'S1：含量99%，限度95%~105%','migration':'无迁移，符合要求','adsorption':'无吸附，符合要求','commitment':'承诺变更后持续开展长期稳定性考察','abnormal_reporting':'承诺上报异常情况',
          'study_object':'自制制剂，不使用参比','planned_batches':'B1、B2、B3','planned_conditions':'30℃','planned_specifications':'1%','planned_packaging':'30ml','planned_times':'0月、24月','planned_items':'含量','planned_standard':'S1','condition_justification':'30℃长期条件匹配密封，30℃以下贮藏，依据包装研究'}
    facts=[fact(k,v) for k,v in vals.items()]+[fact(k,vals[k],'b') for k in COMPARISON_FIELDS]
    subs=[{'doc_id':id,'file_name':id+'.txt','parse_status':'success','extracted_json':{'facts':[f for f in facts if f['source']['doc_id']==id], 'document_types':['label'], 'source_statements':[{'text':'说明书' if id=='a' else '标签','source':{'file_name':id+'.txt'}}]}} for id in ('a','b')]
    label_revision=fact('revision_info',vals['revision_info'],'b');facts.append(label_revision);subs[1]['extracted_json']['facts'].append(label_revision)
    # 形式审查与规则证据分别检验，label两份补齐类型必要字段。
    records=[{**record(b,m),'within_standard':True} for b in ('B1','B2','B3') for m in (0,24)]
    for r in records:
        text=f"检验报告\n报告编号：R{r['batch_no']}{r['month']}\n批号：{r['batch_no']}\n时间点：{r['month']}月\n检测项目：含量\n检验结果：99%\n检验方法：HPLC\n考察条件：30℃\n规格：1%\n包装：30ml\n样品角色：自制制剂\n图谱编号：G{r['batch_no']}{r['month']}"
        s=doc(text,'report'+r['batch_no']+str(r['month']),[])
        s['extracted_json']['document_types']=['inspection'];s['extracted_json']['evidence_images']=[{'image_id':'controlled_image'}]
        # 检验报告必需元数据，测试A的确切报告/图谱对应而非文件名。
        for k,v in {'drug_name':'受控药品','inspection_date':'2025-01-01','test_standard':'S1'}.items():s['extracted_json']['facts'].append(fact(k,v,s['doc_id']))
        subs.append(s)
    form={'item_5_application_matter_category':{'selected_values':['1.7']},'item_30_applicant_info':{'sub_fields':{'applicant_name':'申请企业'}},'item_15_validity_period':{'sub_fields':{'original_validity_period':'18个月','proposed_validity_period':'24个月','storage_condition':vals['storage_condition']}},'item_22_change_reason':{'value':'根据长期研究延长有效期'}}
    st={'records':records,'reference_records':[],'proposed_validity_period':'24个月','coverage':{'requirements_known':True,'missing':[],'groups':[],'reason':'方案对应','requirements_evidence':[]},'limit_check':{'status_code':'within_spec'},'significant_change_assessment':{'status':'通过','reason':'适用阈值内','criteria':[]}}
    return {'facts':facts,'form':form,'submissions':subs,'stability':st,'completeness':{'missing_items':[]},'comparisons':compare_facts(facts),'quality':{}}


class T4Tests(unittest.TestCase):
    def test_five_types_and_reference(self):
        cases=[('approval','药品注册证书\n药品名称：药甲\n批准文号：国药准字H20260001\n剂型：喷雾剂\n规格：1%\n包装：每瓶30ml\n上市许可持有人：甲公司\n生产企业：乙公司\n有效期：24个月\n贮藏条件：不得冷冻，密封','approval_no','国药准字H20260001'),('license','药品生产许可证\n企业名称：乙公司\n许可证编号：浙202601\n生产地址：甲路1号\n生产范围：喷雾剂\n有效期：2026年至2031年','license_valid_until','2026年至2031年'),('standard','药品注册标准\n标准编号：YBH20260001\n检测项目：含量\n限度：95%~105%\n检测方法：HPLC','standard_no','YBH20260001'),('label','说明书\n药品名称：药甲\n规格：1%\n包装：每瓶30ml\n生产企业：乙公司\n有效期：24个月\n贮藏条件：不得冷冻，密封','packaging','每瓶30ml'),('inspection','检验报告\n报告编号：R01\n样品名称：药甲\n批号：B1\n检验日期：2026-01-01\n检验依据：YBH20260001\n检测项目：含量\n检验结果：99%','report_no','R01')]
        for kind,text,field,value in cases:
            with self.subTest(kind=kind):
                d=doc(text,kind);self.assertEqual(d['extracted_json'][field],value);self.assertIn(kind,d['extracted_json']['document_types']);self.assertTrue(all(f['source'].get('page')==1 for f in d['extracted_json']['facts']))
        license=doc(cases[1][1]);self.assertEqual(license['extracted_json']['validity_period'],'')
        reference=doc('化学药物稳定性研究技术指导原则\n批准文号：示例编号');self.assertEqual(collect_facts([reference]),[])
        ex=doc(cases[0][1])['extracted_json'];self.assertEqual(ex['specification'],'1%');self.assertEqual(ex['packaging'],'每瓶30ml')

    def test_comparisons_and_states(self):
        a=fact('specification','１ %');b=fact('specification','1%','b');self.assertEqual([x for x in compare_facts([a,b]) if x['field']=='specification'][0]['status'],'一致')
        b=fact('specification','2%','b');self.assertEqual([x for x in compare_facts([a,b]) if x['field']=='specification'][0]['status'],'不一致')
        a=fact('validity_period','18个月',context='approved');b=fact('validity_period','24个月','b','proposed')
        self.assertEqual([x for x in compare_facts([a,b]) if x['field']=='validity_period'][0]['status'],'无法核验')
        self.assertEqual([x for x in compare_facts([a,b,fact('revision_info','由18延长至24','b')]) if x['field']=='validity_period'][0]['status'],'前后变更')
        ex=extract_document('blank.txt',[{'text':'药品名称：\n规格：1%\n规格：2%'}]);self.assertEqual(ex['field_states']['drug_name'],'blank');self.assertEqual(ex['field_states']['specification'],'conflict');self.assertEqual(ex['field_states']['approval_no'],'not_extracted');self.assertEqual(extract_document('x',[{'text':'生产范围：不适用（主体仅为持有人，依据许可证载明职能）'}])['field_states']['production_scope'],'not_applicable')
        d=doc('药品注册证书\n药品名称：药甲');self.assertTrue(content_completeness([d])[0]);d['latest_attempt']={'content_status':'failed'};self.assertFalse(content_completeness([d])[0]);self.assertTrue(content_completeness([d])[1])

    def test_standard_bridge(self):
        c=good_context();sub=c['submissions'][0];self.assertEqual(bridge_check([sub])['status'],'通过')
        sub['extracted_json']['facts'].append(fact('standard_no','S2'))
        self.assertEqual(bridge_check([sub])['status'],'证据不足')
        sub['extracted_json']['facts'] += [fact('revision_info','S1至S2有关物质方法已修订'),fact('bridge_versions','S1、S2'),fact('bridge_data','旧法99.1%，新法99.2%，差值0.1%'),fact('bridge_conclusion','结果可比，验证通过')]
        self.assertEqual(bridge_check([sub])['status'],'通过')
        sub['extracted_json']['facts'][-1]=fact('bridge_conclusion','结果不可比，不通过');self.assertEqual(bridge_check([sub])['status'],'发现问题')


class T5Tests(unittest.TestCase):
    def test_original_and_fallback_classification(self):
        path=Path(__file__).parents[1]/'task/change_review/T1延长有效期资料24月-发送IT(1).docx'
        chunks=parse_filing_change_docx(str(path));svc=object.__new__(FilingChangeReviewService);ex=svc._extract_structured_payload(path.name,chunks)
        tables=[t for c in chunks for t in c.get('tables',[])];self.assertEqual(sum(t['table_type']=='stability_result' for t in tables),4)
        self.assertTrue(ex['study_plans']);self.assertFalse(any(__import__('re').fullmatch(r'\d+(?:个)?月.*',item) for p in ex['study_plans'] for item in p['indicators']))
        self.assertEqual(tables[-1]['table_type'],'standard_version_timeline')
        for kind,headers,rows in [('standard_version_timeline',['考察时间','检测标准','变化内容及依据'],[['24月','S2','新方法']]),('study_plan',['时间点','检测项目','评价标准'],[['24月','含量','S1']]),('report_explanation',['批号','报告单编号','质量标准','报告时间'],[['B1','R1','S1','24月']])]:
            caption='研究方案' if kind=='study_plan' else ''
            self.assertEqual(classify_table([headers]+rows,caption),kind)
            self.assertEqual(FilingChangeStabilityService()._build_stability_records([{'headers':headers,'rows':rows,'section_title':caption}]),[])
        st=FilingChangeStabilityService().run({'form_json':{'item_15_validity_period':{'sub_fields':{'proposed_validity_period':'24个月'}}}},[{'doc_id':'original','file_name':path.name,'parse_status':'success','extracted_json':ex}])
        self.assertEqual(st['record_count'],450);self.assertEqual(st['reference_record_count'],66)
        self.assertEqual({f['raw_value'] for f in ex['facts'] if f['field']=='specification'},{'2%','1％'});self.assertNotIn('检测标准',[r['indicator'] for r in st['records']]);self.assertTrue(all('source_column_index' in r for r in st['records']))
        OUT.mkdir(exist_ok=True);(OUT/'current_extracted.json').write_text(json.dumps(ex,ensure_ascii=False,indent=2));(OUT/'current_stability.json').write_text(json.dumps(st,ensure_ascii=False,indent=2))

    def test_group_coverage_and_units(self):
        svc=FilingChangeStabilityService();records=[record('B1',0),record('B1',24),record('B2',0),record('B2',18),record('B1',0,'25℃','60ml'),record('B1',18,'25℃','60ml')]
        plans=[{'product_role':'自制制剂','batch_no':b,'condition':c,'specification':'1%','packaging':p,'time_points':[0,24],'indicators':['含量'],'standard':'S1','sources':[]} for b,c,p in [('B1','30℃','30ml'),('B2','30℃','30ml'),('B1','25℃','60ml')]]
        sub={'doc_id':'s','file_name':'稳定性.txt','extracted_json':{'stability_records':records,'study_plans':plans}}
        st=svc.run({'form_json':{'item_15_validity_period':{'sub_fields':{'proposed_validity_period':'24个月'}}}},[sub]);gaps=' '.join(st['coverage']['missing']);self.assertIn('B2',gaps);self.assertIn('60ml',gaps);self.assertTrue(st['coverage']['requirements_known']);self.assertEqual(len(st['key_indicators'][0]['series_summaries']),3)
        for result,limit,expected in [('99%','95%~105%',True),('<10cfu/ml','≤100cfu/ml',True),('ND','≤0.2%',None),('ND','不得检出',True),('无色澄清液体','无色至淡黄色澄清液体',True),('含量:99%;杂质:0.1%','含量:95%~105%;杂质:≤0.2%',True),('不符合规定','应符合规定',False),('99%; RSD=2%','95%~105%',None)]:
            with self.subTest(result=result):self.assertIs(svc._evaluate_limit(result,limit)['within_standard'],expected)
        self.assertIsNone(svc._extract_numeric('ND'));self.assertIsNone(svc._extract_numeric('<10cfu/ml'))
        self.assertIsNone(st['key_indicators'][0]['significant_change'])

    def test_significance_and_record_conflict(self):
        svc=FilingChangeStabilityService();rows=[svc._enrich_record(record(month=0,value='100%')),svc._enrich_record(record(month=24,value='94%'))]
        criteria=fact('significant_criteria','项目=含量；适用条件=30℃；下降>5%；依据=S1')
        d={'extracted_json':{'facts':[criteria]}}
        a=svc._significant_assessment(rows,[d]);self.assertEqual(a['status'],'发现问题')
        rows[-1]=svc._enrich_record(record(month=24,value='99%'));self.assertEqual(svc._significant_assessment(rows,[d])['status'],'通过')
        self.assertEqual(svc._significant_assessment(rows,[])['status'],'证据不足')
        rows.append(svc._enrich_record(record(month=24,value='98%')));self.assertTrue(svc._record_conflicts(rows))


class T6Tests(unittest.TestCase):
    def evaluate(self,code,ctx,rule=None):
        service=FilingChangeRuleReviewService();rule=rule or definitions()[code]
        result=service.run({'application_form':ctx['form'],'facts':ctx['facts']},ctx['submissions'],[rule],ctx['completeness'],ctx['stability'],ctx.get('quality',{}))
        return result['rule_results'][0]

    def test_all_19_support_insufficient_outside(self):
        matrix=[]
        for code in definitions():
            with self.subTest(code=code):
                ctx=good_context()
                if code=='TVS-FORM-002':ctx['facts'].append(fact('specification','2%','second-spec'))
                supported=self.evaluate(code,ctx);self.assertEqual(supported['execution_status'],'completed');self.assertEqual(supported['status'],'通过',supported)
                empty={'facts':[],'form':{},'submissions':[],'stability':{},'completeness':{},'comparisons':[]}
                insufficient=self.evaluate(code,empty);self.assertEqual(insufficient['status'],'证据不足',insufficient)
                ctx['form']['item_5_application_matter_category']['selected_values']=['1.1'];outside=self.evaluate(code,ctx);self.assertEqual(outside['status'],'不适用')
                matrix.append({'rule_code':code,'supported':supported['status'],'insufficient':insufficient['status'],'outside':outside['status'],'subchecks':[s['name'] for s in supported['subchecks']]})
        OUT.mkdir(exist_ok=True);(OUT/'rule_matrix.json').write_text(json.dumps(matrix,ensure_ascii=False,indent=2))

    def test_counterexamples(self):
        results=[]
        for code in definitions():
            ctx=good_context()
            def replace_field(key,value):
                for f in ctx['facts']:
                    if f['field']==key:f.update(raw_value=value,normalized_value=normalize(value))
            if code=='TVS-FR-001':ctx['completeness']['missing_items']=[{'label':'批准证明','message':'本次未选入'}]
            elif code=='TVS-FR-002':ctx['form']['item_30_applicant_info']['sub_fields']['applicant_name']='受托生产企业'
            elif code=='TVS-CONS-001':ctx['facts'].append(fact('approval_no','国药准字H99999999','wrong'))
            elif code=='TVS-FORM-001':ctx['form']['item_15_validity_period']['sub_fields']['proposed_validity_period']=''
            elif code=='TVS-FORM-002':ctx['facts'].append(fact('specification','2%','second'));ctx['stability']['coverage']['missing']=['第二规格缺末点']
            elif code=='TVS-TECH-001':replace_field('approved_process','另一个工艺')
            elif code=='TVS-TECH-002':replace_field('packaging','60ml')
            elif code=='TVS-TECH-003':replace_field('commercial_scale','300kg')
            elif code=='TVS-STAB-001':ctx['stability']['records']=ctx['stability']['records'][:2]
            elif code=='TVS-STAB-002':replace_field('condition_justification','30℃与密封，30℃以下贮藏不匹配，依据研究')
            elif code=='TVS-STAB-003':ctx['stability']['coverage']['missing']=['B2 24月含量缺失']
            elif code=='TVS-QLT-001':replace_field('method_validation','未验证，不通过')
            elif code=='TVS-QLT-002':ctx['stability']['records'][0]['within_standard']=False
            elif code=='TVS-STAB-004':ctx['stability']['significant_change_assessment']['status']='发现问题'
            elif code=='TVS-TECH-004':ctx['stability']['coverage']['missing']=['B2末点缺失']
            elif code=='TVS-TECH-005':ctx['facts'].append(fact('storage_condition','不得冷冻','wrong'))
            elif code=='TVS-TECH-006':ctx['form']['item_15_validity_period']['sub_fields']['proposed_validity_period']='36个月'
            elif code=='TVS-RISK-001':replace_field('compatibility','不相容，有迁移')
            elif code=='TVS-POST-001':replace_field('commitment','拒绝持续考察')
            with self.subTest(code=code):
                result=self.evaluate(code,ctx);self.assertEqual(result['status'],'发现问题',result);results.append({'rule_code':code,'counterexample':result['status']})
        (OUT/'rule_counterexamples.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))

    def test_parameters_and_isolated_failure(self):
        ctx=good_context();r=definitions()['TVS-STAB-001'];r['rule_condition']={'minimum_batches':4};self.assertEqual(self.evaluate(r['rule_code'],ctx,r)['status'],'发现问题')
        r['rule_condition']={'minimum_batches':2};self.assertEqual(self.evaluate(r['rule_code'],ctx,r)['status'],'通过')
        r['rule_condition']='永远通过';self.assertTrue(validate_rule(r))
        service=FilingChangeRuleReviewService();original=service._evaluate_rule
        def faulty(rule,context):
            if rule['rule_code']=='TVS-STAB-001':raise RuntimeError('受控单条失败')
            return original(rule,context)
        with patch.object(service,'_evaluate_rule',side_effect=faulty):
            result=service.run({'application_form':ctx['form'],'facts':ctx['facts']},ctx['submissions'],list(definitions().values()),ctx['completeness'],ctx['stability'])
        self.assertEqual(len(result['rule_results']),19);self.assertEqual(result['rule_summary']['执行失败'],1);self.assertIsNone(next(r for r in result['rule_results'] if r['execution_status']=='failed')['status'])
        self.assertEqual(sum(v for k,v in result['rule_summary'].items() if k!='total'),19)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        from agent.test.test_filing_change_review_service_regression import _TestConnection
        from agent.agent_backend.services.filing_change_material_service import FilingChangeMaterialService
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.s=object.__new__(FilingChangeReviewService);self.s.root_dir=self.root/'review';self.s.db_conn=_TestConnection(self.root/'test.sqlite');self.s.material_service=FilingChangeMaterialService();self.s._ensure_dirs()
        ok,msg,p=self.s.create_project({'project_name':'T4受控范围'});self.assertTrue(ok,msg);self.pid=p['project_id']

    def tearDown(self):self.s.db_conn.engine.dispose();self.tmp.cleanup()

    def upload(self,name,text,category):
        from agent.test.test_filing_change_review_service_regression import _Upload
        ok,msg,data=self.s.upload_submission_files(self.pid,[_Upload(name,text.encode())],category);self.assertTrue(ok,msg)
        docid=data['created'][0]['doc_id'] if 'created' in data else data['files'][0]['doc_id']
        return docid

    def test_manual_scope_completeness_and_not_applicable(self):
        d=self.upload('批准证明.txt','药品注册证书\n药品名称：药甲\n批准文号：国药准字H20260001','5')
        ok,msg,_=self.s.parse_submission(self.pid,d);self.assertTrue(ok,msg)
        meta=self.s._load_submission_manifest(self.pid)['doc_meta'][d]
        self.assertEqual(meta['material_category'],'5');self.assertFalse(meta['auto_classified']);self.assertTrue(meta['classification_difference'])
        self.s.update_submission_metadata(self.pid,d,{'material_category':'1','material_sub_category':''})
        c=self.s.check_submission_completeness(self.pid);self.assertFalse(any(x['code']=='1' for x in c['missing_items']));self.assertTrue(c['content_missing'])
        self.s.update_submission_metadata(self.pid,d,{'review_enabled':False});c=self.s.check_submission_completeness(self.pid);self.assertTrue(any(x['code']=='1' for x in c['missing_items']));self.assertFalse(c['content_missing'])
        self.s.set_category_not_applicable(self.pid,{'category_code':'7','reason':'本次仅延长有效期，无临床变更；依据本次变更范围'})
        c=self.s.check_submission_completeness(self.pid);self.assertEqual(c['counts']['not_applicable'],1)

    def test_rule_saved_condition_is_executed_and_invalid_rejected(self):
        from agent.agent_backend.services.filing_change_rule_service import FilingChangeRuleService
        from agent.agent_backend.database.mysql.db_model import FilingChangeReviewRule
        FilingChangeReviewRule.__table__.create(self.s.db_conn.engine)
        rules=FilingChangeRuleService(self.s.db_conn);ok,msg,r=rules.create_rule(definitions()['TVS-STAB-001']);self.assertTrue(ok,msg)
        ok,msg,_=rules.update_rule(r['rule_id'],{'rule_condition':{'minimum_batches':4}});self.assertTrue(ok,msg)
        saved=rules.list_rules({})['list'][0];ctx=good_context();result=T6Tests().evaluate(saved['rule_code'],ctx,saved);self.assertEqual(result['status'],'发现问题');self.assertEqual(result['applied_condition'],{'minimum_batches':4})
        ok,msg,_=rules.update_rule(r['rule_id'],{'rule_condition':'全部通过'});self.assertFalse(ok);self.assertEqual(rules.list_rules({})['list'][0]['rule_json']['rule_condition'],{'minimum_batches':4})
