"""19条备案规则的唯一执行入口。业务判断与执行状态分开保存。"""
import re
from pathlib import Path
import json
from agent.agent_backend.services.filing_change_extraction_service import collect_facts, compare_facts, content_completeness, normalize, is_submission, FIELD_LABELS
from agent.agent_backend.services.filing_change_quality_check_service import bridge_check


def definitions():
    path=Path(__file__).resolve().parents[2]/'task/change_review/审评规则_变更有效期和贮藏条件_技术审评要点版.json'
    return {r['rule_code']:r for r in json.loads(path.read_text())['rules']}


def validate_rule(rule):
    merged={**rule,**(rule.get('rule_json') or {})};code=rule.get('rule_code');template=definitions().get(code)
    if not template:return '当前执行器不支持该规则标识，请使用本轮19条规则'
    condition=merged.get('rule_condition',template['rule_condition'])
    if isinstance(condition,str) and condition!=template['rule_condition']:return '自由文本条件无法执行；请保留原条件，批次数可用参数 minimum_batches 修改'
    if isinstance(condition,dict):
        allowed={'minimum_batches'} if code=='TVS-STAB-001' else set()
        if set(condition)-allowed:return '条件包含不支持的参数'
        n=condition.get('minimum_batches',3)
        if isinstance(n,bool) or not isinstance(n,int) or n<1 or n>100:return 'minimum_batches 必须是1至100的整数'
    elif not isinstance(condition,str):return '规则条件必须为支持的参数对象或原规则条件'
    if rule.get('rule_content',template['rule_content']) != template['rule_content']:
        return '当前规则内容绑定已实现的业务检查；自由改写不改变执行逻辑，请保留规则内容并编辑支持的参数'
    return ''


def check(name,status,reason,evidence=None):
    return {'name':name,'status':status,'reason':reason,'evidence':evidence or []}


def combined(checks):
    states=[x['status'] for x in checks]
    return '发现问题' if '发现问题' in states else ('证据不足' if '证据不足' in states else ('不适用' if states and all(x=='不适用' for x in states) else '通过'))


class FilingChangeRuleReviewService:
    def run(self, extracted, submissions, rules, completeness, stability=None, quality=None):
        form=extracted.get('application_form',{}); submissions=[s for s in submissions if is_submission(s)]
        facts=extracted.get('facts') or collect_facts(submissions)
        comparisons=compare_facts(facts)
        context={'form':form,'submissions':submissions,'facts':facts,'comparisons':comparisons,'completeness':completeness or {},'stability':stability or {},'quality':quality or {}}
        results=[]
        for rule in rules:
            if rule.get('enabled') is False:continue
            merged={**rule,**(rule.get('rule_json') or {})}
            result={**{k:merged.get(k,'') for k in ('rule_id','rule_code','rule_name','rule_category','rule_type','rule_content','basis_source','hit_result')},'applied_condition':merged.get('rule_condition'),'applicability':{'task_type':merged.get('task_type'),'drug_category':merged.get('drug_category'),'change_item':merged.get('applicable_change_item')}}
            try:
                error=validate_rule(rule)
                if error:raise ValueError(error)
                reason=self._outside_scope(merged,form)
                checks=[check('适用范围','不适用',reason)] if reason else self._evaluate_rule(merged,context)
                result.update(execution_status='completed',status=combined(checks),subchecks=checks,facts=[x['reason'] for x in checks],evidence=[e for c in checks for e in c['evidence']],review_conclusion='；'.join(x['name']+'：'+x['status']+'，'+x['reason'] for x in checks))
            except Exception as exc:
                result.update(execution_status='failed',status=None,subchecks=[],facts=[],review_conclusion='执行失败：'+str(exc),execution_error=str(exc))
            results.append(result)
        content,parse=content_completeness(submissions)
        missing=list((completeness or {}).get('missing_items',[]))
        errors=[r for r in results if r['execution_status']!='completed']
        issue_rules=[r for r in results if r.get('status') in ('发现问题','证据不足') or r['execution_status']=='failed']
        return {'formal_review':{'result':'需人工确认' if parse or errors else ('需补正' if missing or content else '通过'),'missing_materials':missing,'content_missing':content,'parse_issues':parse,'not_applicable':completeness.get('not_applicable',[]),'counts':completeness.get('counts',{}),'incomplete_items':[],'suggestions':[]},'field_check':{'facts':facts},'consistency_check':{'comparisons':comparisons,'consistent_items':[x['field'] for x in comparisons if x['status']=='一致'],'inconsistent_items':list(dict.fromkeys(x.get('field_label',x['field'])+'：'+x['left']['raw_value']+' 与 '+x['right']['raw_value']+' 不一致' for x in comparisons if x['status']=='不一致')),'need_manual_review':[x['field'] for x in comparisons if x['status']=='无法核验']},'matched_rules':issue_rules,'rule_results':results,'rule_summary':{**{state:sum(r.get('status')==state for r in results) for state in ('通过','发现问题','证据不足','不适用')},'执行失败':len(errors),'total':len(results)}}

    @staticmethod
    def _outside_scope(rule, form):
        if rule.get('task_type') not in ('','extend_validity_period',None):return '规则任务类型不属于本次延长有效期审评'
        matters=(form.get('item_5_application_matter_category') or {}).get('selected_values',[])
        if matters and '1.7' not in matters:return '申请表明确选择其他变更事项'
        category=(form.get('item_2_drug_category') or {}).get('value') or form.get('drug_category','')
        if category and rule.get('drug_category') and rule['drug_category']!=category:return '药品类别与规则适用类别不一致'
        scope=rule.get('applicable_change_item','')
        if scope and not any(x in scope for x in ('延长','有效期','贮藏')):return '规则变更事项范围不包含本次申请'
        return ''

    def _evaluate_rule(self, rule, ctx):
        code=rule['rule_code'];facts=ctx['facts'];form=ctx['form'];st=ctx['stability'];subs=ctx['submissions']
        def get(field):return [f for f in facts if f['field']==field and f['status']=='available' and f.get('availability')=='current']
        def need(name,fields):
            missing=[k for k in fields if not get(k)]
            return check(name,'证据不足' if missing else '通过','缺少：'+','.join(FIELD_LABELS.get(k,[k])[0] for k in missing) if missing else '原件包含各项可定位信息',sum((get(k) for k in fields),[]))
        def pair(name,a,b,justification=None):
            av,bv=get(a),get(b);checks=[]
            if not av or not bv:return check(name,'证据不足','缺少样品与批准资料两端的对应值',av+bv)
            for left in av:
                source=left['source'];batch=source.get('batch_no')
                candidates=[]
                for right in bv:
                    rs=right['source'];rb=rs.get('batch_no')
                    role=source.get('product_role');rr=rs.get('product_role')
                    if role and rr and role!=rr:continue
                    applicable=[f for f in get('approved_batches') if f['source'].get('doc_id')==rs.get('doc_id')]
                    explicit=any(batch and batch in re.split(r'[、,，;；]',f['raw_value']) for f in applicable)
                    if (batch and rb==batch) or (not batch and not rb and len({f['normalized_value'] for f in av})==1 and len({f['normalized_value'] for f in bv})==1) or explicit:candidates.append(right)
                ev=[left]+candidates
                if len({f['normalized_value'] for f in candidates})!=1:
                    checks.append(check(name,'证据不足',f'{batch or "研究对象"}：批准依据对应不明确，需明确适用批次/样品',ev));continue
                right=candidates[0]
                if left['normalized_value']==right['normalized_value']:
                    checks.append(check(name,'通过',f'{batch or "研究对象"}：样品 {left["raw_value"]} 与批准 {right["raw_value"]} 一致',ev));continue
                impact=[f for f in get(justification) if f['source'].get('doc_id')==source.get('doc_id') and f['source'].get('batch_no') in (None,'',batch)] if justification else []
                data=[f for f in get('impact_data')+get('impact_basis') if (not batch or f['source'].get('batch_no')==batch) and any(f['source'].get('doc_id')==i['source'].get('doc_id') for i in impact)]
                assessment=self._research_evidence('影响论证',impact,data)
                status='通过' if assessment['status']=='通过' else ('证据不足' if impact and assessment['status']=='证据不足' else '发现问题')
                checks.append(check(name,status,f'{batch or "研究对象"}：样品 {left["raw_value"]}；批准 {right["raw_value"]}；'+assessment['reason'],ev+impact+data))
            result=check(name,combined(checks),'；'.join(c['reason'] for c in checks),[f for c in checks for f in c['evidence']]);result['comparisons']=checks
            return result
        def conclusion(name,fields):
            item=need(name,fields)
            if item['status']!='通过':return item
            text=' '.join(f['raw_value'] for f in item['evidence'])
            if re.search(r'不符合|不通过|不可比|未验证|有迁移|存在吸附|不相容|拒绝',text):item.update(status='发现问题',reason='资料存在明确反例：'+text)
            elif not re.search(r'符合|通过|可比|不影响|无迁移|无吸附|相容|承诺',text):item.update(status='证据不足',reason='已有材料但结论或支持数据不足')
            return item
        records=st.get('records',[])
        if code=='TVS-FR-001':
            c=ctx['completeness']; missing=c.get('missing_items',[]);content,parse=content_completeness(subs)
            return [check('文件齐全性','发现问题' if missing else ('通过' if subs and 'missing_items' in c else '证据不足'),str(missing) if missing else '本次选入材料覆盖目录必需类别',missing or [{'source': {'doc_id': s.get('doc_id'), 'file_name': s.get('file_name')}, 'raw_value': '已选入材料：' + str(s.get('material_category', ''))} for s in subs]),check('内容完整性','证据不足' if parse else ('发现问题' if content else '通过'),str(parse or content) if parse or content else '已识别材料必要内容齐全',parse or content or facts)]
        if code=='TVS-FR-002':
            applicant=get('applicant')
            val=((form.get('item_30_applicant_info') or {}).get('sub_fields') or {}).get('applicant_name')
            holder=get('holder');manufacturer=get('manufacturer')
            if not val or not holder:return [check('申请主体与MAH对应','证据不足','申请人或批准证明中的持有人未可靠提取，不能判主体不合规',applicant+holder)]
            ok=normalize(val) in {f['normalized_value'] for f in holder}
            return [check('申请主体与MAH对应','通过' if ok else '发现问题','申请人：'+val+'；批准持有人：'+str([f['raw_value'] for f in holder]),holder)]
        if code=='TVS-CONS-001':
            return [check(x.get('field_label',x['field']),{'一致':'通过','前后变更':'通过','不一致':'发现问题','无法核验':'证据不足'}[x['status']],x['reason'],[v for v in (x['left'],x['right']) if v]) for x in ctx['comparisons']]
        if code=='TVS-FORM-001':
            v=(form.get('item_15_validity_period') or {}).get('sub_fields') or {}
            missing=[k for k in ('original_validity_period','proposed_validity_period','storage_condition') if not v.get(k)]
            if not (form.get('item_22_change_reason') or {}).get('value'):missing.append('变更原因与依据')
            return [check('变更前后事项',('发现问题' if (form.get('item_5_application_matter_category') or {}).get('selected_values') else '证据不足') if missing else '通过','缺少：'+','.join(missing) if missing else str(v),[{'source':'application_form','value':v}])]
        if code=='TVS-FORM-002':
            specs=get('specification');packs=get('packaging')
            if not specs or not packs:return [check('多规格多包装对应','证据不足','缺少规格或包装，无法确定是否多规格/多包装',specs+packs)]
            if len({f['normalized_value'] for f in specs})==1 and len({f['normalized_value'] for f in packs})==1:return [check('多规格多包装对应','不适用','资料明确仅有一种规格和包装',specs+packs)]
            return [check('多规格多包装对应','证据不足' if not st.get('coverage',{}).get('requirements_known') else ('发现问题' if st['coverage']['missing'] else '通过'),st.get('coverage',{}).get('reason','需要逐规格/包装方案与实际记录对应'),specs+packs)]
        if code=='TVS-TECH-001':
            return [pair('D 样品处方','prescription','approved_prescription','impact_assessment'),pair('D 样品生产工艺','process','approved_process','impact_assessment'),pair('D 关键参数','critical_parameters','approved_parameters','impact_assessment')]
        if code=='TVS-TECH-002':
            expected={f['normalized_value'] for f in get('packaging')};actual={normalize(r.get('packaging')) for r in records if r.get('packaging') not in ('',None,'/')}
            status='证据不足' if not expected or not actual else ('通过' if expected==actual else '发现问题')
            return [check('逐包装研究对应',status,'申报包装：'+str(sorted(expected))+'；研究包装：'+str(sorted(actual)),get('packaging')+records)]
        if code=='TVS-TECH-003':return [pair('D 样品批量与批准规模','batch_size','commercial_scale','representativeness')]
        if code=='TVS-STAB-001':
            batches=sorted({r.get('batch_no') for r in records if r.get('batch_no') and r.get('product_role') in ('自制制剂','自制品','自制')})
            condition=rule.get('rule_condition');minimum=condition.get('minimum_batches',3) if isinstance(condition,dict) else 3
            sequence=need('连续生产及商业化规模',['batch_sequence','batch_size','commercial_scale'])
            text=' '.join(f['raw_value'] for f in get('batch_sequence'))
            if sequence['status']=='通过':
                if '不连续' in text:sequence.update(status='发现问题',reason='原文明确批次不连续')
                elif '连续' not in text or not all(batch in text for batch in batches):sequence.update(status='证据不足',reason='批次顺序未明确关联全部样品的连续生产情况')
                elif {f['normalized_value'] for f in get('batch_size')}!={f['normalized_value'] for f in get('commercial_scale')}:sequence.update(status='发现问题',reason='研究批量与批准商业化规模不一致')
            return [check('可追溯批次数','发现问题' if batches and len(batches)<minimum else ('通过' if len(batches)>=minimum else '证据不足'),f'识别批次 {batches}，要求至少 {minimum} 批',records),sequence]
        if code=='TVS-STAB-002':
            conditions=sorted({r.get('condition') for r in records if r.get('condition')})
            storage=((form.get('item_15_validity_period') or {}).get('sub_fields') or {}).get('storage_condition')
            ev=get('condition_justification');text=' '.join(f['raw_value'] for f in ev)
            status='证据不足'
            if ev and storage and conditions and all(normalize(c) in normalize(text) for c in conditions) and normalize(storage) in normalize(text):status='发现问题' if re.search(r'不匹配|不支持',text) else ('通过' if '匹配' in text or '支持' in text else '证据不足')
            return [check('长期条件与贮藏要求',status,'长期条件：'+str(conditions)+'；拟贮藏：'+str(storage)+'；需有品种及包装适用依据',ev)]
        if code=='TVS-STAB-003':
            coverage=st.get('coverage',{})
            return [check('项目及时间点覆盖','发现问题' if coverage.get('missing') else ('通过' if coverage.get('requirements_known') else '证据不足'),str(coverage.get('missing')) if coverage.get('missing') else coverage.get('reason','缺少适用方案'),coverage.get('requirements_evidence',[])),*self._plan_checks(ctx),*self._report_checks(ctx)]
        if code=='TVS-QLT-001':
            b=bridge_check(subs);return [check('标准与方法桥接',b['status'],b['reason'],b['evidence'])]
        if code=='TVS-QLT-002':
            bad=[r for r in records if r.get('within_standard') is False];unknown=[r for r in records if r.get('within_standard') is None and r.get('result_text') not in ('NA','N/A')]
            return [check('逐条限度符合性','发现问题' if bad else ('证据不足' if unknown or not records or any(r.get('available_status') in ('failed','partial','pending') for r in records) else '通过'),f'超限 {len(bad)} 条，待判断 {len(unknown)} 条，详见逐条位置',bad or unknown or records)]
        if code=='TVS-STAB-004':
            a=st.get('significant_change_assessment',{});return [check('显著变化',a.get('status','证据不足'),a.get('reason','缺少适用判定依据'),a.get('assessments',[])+a.get('criteria',[]))]
        if code=='TVS-TECH-004':
            c=st.get('coverage',{});return [check('逐组有效期支持','发现问题' if c.get('missing') else ('通过' if c.get('requirements_known') and st.get('limit_check',{}).get('status_code')=='within_spec' else '证据不足'),str(c.get('missing')) if c.get('missing') else '需结合逐组覆盖、限度及显著变化支持拟定有效期',c.get('groups',[])),*self._reference_checks(ctx)]
        if code=='TVS-TECH-005':
            comparisons=[x for x in ctx['comparisons'] if x['field']=='storage_condition'];storage=get('storage_condition')
            if any(x['status']=='不一致' for x in comparisons):return [check('贮藏表述与标签','发现问题','贮藏条件原值不一致',storage)]
            valid=storage and all(re.search(r'常温|阴凉|冷藏|密封|避光|遮光|℃',x['raw_value']) for x in storage)
            return [check('贮藏表述与标签','通过' if valid and all(x['status'] in ('一致','前后变更') for x in comparisons) else '证据不足','逐条保留条件、否定和温度要求，核对说明书与标签',storage)]
        if code=='TVS-TECH-006':
            checks=[]
            target=((form.get('item_15_validity_period') or {}).get('sub_fields') or {}).get('proposed_validity_period')
            for kind,title in [('instructions','说明书'),('package_label','标签')]:
                documents=[]
                for sub in subs:
                    ex=sub.get('extracted_json') or {};kinds=ex.get('label_kinds',[])
                    statements='\n'.join(x['text'] for x in ex.get('source_statements',[]))
                    pattern=r'^(?:修订后|药品)?说明书(?:样稿)?[：:]?$' if kind=='instructions' else r'^(?:药品)?标签(?:样稿)?[：:]?$'
                    if kind in kinds or re.search(pattern,statements,re.M):documents.append(sub)
                ev=[f for f in facts if f['source'].get('doc_id') in {d['doc_id'] for d in documents} and f['source'].get('material_component') in (None,'',kind)]
                missing=[k for k in ('revision_info','validity_period','storage_condition') if not any(f['field']==k and f['status']=='available' for f in ev)]
                bad=[f for f in ev if f['field']=='validity_period' and target and normalize(target).replace('个月','月')!=f['normalized_value'].replace('个月','月')]
                storage=[f for f in ev if f['field']=='storage_condition'];expected=((form.get('item_15_validity_period') or {}).get('sub_fields') or {}).get('storage_condition')
                bad += [f for f in storage if expected and normalize(expected)!=f['normalized_value']]
                checks.append(check(title+'同步修订','证据不足' if not documents or missing else ('发现问题' if bad else '通过'),'缺少内容确认的'+title if not documents else ('缺少：'+','.join(missing) if missing else ('有效期/贮藏与申请不一致' if bad else '已按内容确认材料并核对修订、有效期和贮藏')),ev))
            return checks
        if code=='TVS-RISK-001':
            checks=[conclusion('包材合法登记',['packaging_registration','packaging_registration_status'])]
            for field,title in [('compatibility','相容性'),('migration','迁移'),('adsorption','吸附')]:
                conclusions=get(field)
                applicability=[f for f in facts if f['field']=='research_applicability' and title in f['raw_value']]
                if any(re.search(r'不适用.*(?:依据|因|理由)',f['raw_value']) for f in applicability):
                    checks.append(check(title+'研究','不适用','明确的研究适用性说明',applicability));continue
                data=get(field+'_data')+(get('compatibility_basis') if field=='compatibility' else [])
                # 结论与实际数据须来自同一材料，或明确引用同一研究编号。
                data=[f for f in data if any(f['source'].get('doc_id')==c['source'].get('doc_id') for c in conclusions)]
                checks.append(self._research_evidence(title+'研究',conclusions,data))
            return checks
        if code=='TVS-POST-001':return [conclusion('持续考察及异常上报承诺',['commitment','abnormal_reporting'])]
        raise ValueError('规则无执行分支：'+code)

    @staticmethod
    def _research_evidence(name,conclusions,data):
        ev=conclusions+data;text=' '.join(f['raw_value'] for f in conclusions)
        pending=r'尚无|尚待|未开展|尚未|未完成|待确认|无.*数据|缺少.*依据'
        if not conclusions or re.search(pending,text+' '+' '.join(f['raw_value'] for f in data)):
            return check(name,'证据不足','研究/论证未完成或缺少可关联的实际数据与依据',ev)
        if re.search(r'不相容|不符合|不通过|有迁移|存在吸附|影响质量',text) and not re.search(r'不影响质量',text):
            return check(name,'发现问题','已有结论明确不支持：'+text,ev)
        measured=any(re.search(r'\d+(?:\.\d+)?\s*(?:%|mg|ug|μg|kg|℃)',f['raw_value']) for f in data)
        if not measured or not any(re.search(r'依据|限度|标准',f['raw_value']) for f in data) or not re.search(r'符合|通过|无迁移|无吸附|相容|不影响',text):
            return check(name,'证据不足','缺少同研究对象的实测数据、适用依据或肯定结论',ev)
        return check(name,'通过','结论与同材料可定位的研究实测数据相互关联',ev)

    def _plan_checks(self,ctx):
        facts=ctx['facts'];fields=('study_object','planned_batches','planned_conditions','planned_specifications','planned_packaging','planned_times','planned_items','planned_standard')
        ev=[f for f in facts if f['field'] in fields and f['status']=='available'];missing=[k for k in fields if not any(f['field']==k for f in ev)]
        gaps=ctx['stability'].get('coverage',{}).get('missing',[])
        return [check('C 稳定性研究方案','证据不足' if missing or not ctx['stability'].get('coverage',{}).get('requirements_known') else ('发现问题' if gaps else '通过'),'缺少方案内容：'+','.join(missing) if missing else ('方案与实际检测存在缺口：'+str(gaps) if gaps else '方案各维度已提取并与逐组检测对应'),ev)]

    def _report_checks(self,ctx):
        records=ctx['stability'].get('records',[]);target=ctx['stability'].get('proposed_validity_period','');m=re.search(r'\d+',target);end=float(m.group()) if m else None
        reports=[];charts=[]
        for s in ctx['submissions']:
            ex=s.get('extracted_json') or {};facts=ex.get('facts',[])
            if any(f.get('source',{}).get('doc_id') not in (None,'',s.get('doc_id')) for f in facts):continue
            def vals(field):return {f['normalized_value'] for f in facts if f['field']==field and f['status']=='available'}
            if 'inspection' in ex.get('document_types',[]) and vals('report_no') and vals('result_text'):reports.append((s,vals('batch_no'),vals('time_point'),vals('test_items'),*[vals(k) for k in ('condition','specification','packaging','product_role')]))
            if vals('chromatogram_id') and ex.get('evidence_images'):charts.append((s,vals('batch_no'),vals('time_point'),vals('test_items'),*[vals(k) for k in ('condition','specification','packaging','product_role')]))
        gaps=[];ev=[]
        required={(r.get('batch_no'),r.get('month'),r.get('indicator'),r.get('condition'),r.get('specification'),r.get('packaging'),r.get('product_role')) for r in records if r.get('month') is not None and r.get('result_text') not in ('NA','N/A','/','')}
        for batch,month,item,condition,specification,packaging,role in sorted(required,key=str):
            dimensions=(condition,specification,packaging,role)
            matched=lambda row:normalize(batch) in row[1] and len(row[1])==1 and len(row[2])==1 and any(str(int(month))+'月'==v or str(month)==v for v in row[2]) and any(normalize(item) in v for v in row[3]) and all(value not in ('',None,'/') and {normalize(value)}==row[index+4] for index,value in enumerate(dimensions))
            if month in (0,end) and not any(matched(x) for x in reports):gaps.append(f'{batch} / {month:g}月 / {item}：缺少关联检验报告')
            # 本轮仅核对有色谱方法或原文明示需图谱的项目，不声称已做图谱质量分析。
            methods=[]
            for submission in ctx['submissions']:
                ff=(submission.get('extracted_json') or {}).get('facts',[])
                if any(f['field']=='test_items' and normalize(item)==f['normalized_value'] for f in ff):
                    methods.extend(f['raw_value'] for f in ff if f['field']=='test_method')
            needs_chart=any(re.search(r'HPLC|GC|色谱|TLC',method,re.I) for method in methods)
            if not methods:gaps.append(f'{batch} / {month:g}月 / {item}：缺少可关联的检测方法，图谱适用性无法核验')
            if needs_chart and not any(matched(x) for x in charts):gaps.append(f'{batch} / {month:g}月 / {item}：缺少关联图谱')
        ev=[{'doc_id':s['doc_id'],'file_name':s['file_name'],'availability':'此前有效结果，本次重解析失败' if (s.get('latest_attempt') or {}).get('content_status')=='failed' else '当前有效结果','latest_attempt':s.get('latest_attempt',{}),'facts':collect_facts([s])} for s,*_ in reports+charts]
        return [check('A 0月末点报告及各时间图谱','证据不足' if not required or gaps else '通过','；'.join(gaps) if gaps else ('报告/图谱归属与覆盖已核对；使用状态及此前有效来源见证据；未实施专业图谱质量分析' if required else '缺少可追溯批次、项目和时间点要求'),ev)]

    def _reference_checks(self,ctx):
        records=ctx['stability'].get('reference_records',[]);facts=ctx['facts']
        ev=[f for f in facts if f['field'] in ('reference_identity','reference_batch','reference_validity') and f['status']=='available']
        if not records and any(f['field']=='study_object' and '不使用参比' in f['raw_value'] for f in facts):return [check('B 参比制剂有效期证明','不适用','研究设计明确不使用参比制剂',[f for f in facts if f['field']=='study_object'])]
        if not records:return [check('B 参比制剂有效期证明','证据不足','未明确当前研究是否使用参比制剂，需要研究设计说明适用性',ev)]
        checks=[]
        for record in records:
            batch=record.get('batch_no');identity=record.get('reference_identity') or record.get('drug_name') or record.get('sample_name')
            if not identity:
                study=[f for f in facts if f['field']=='reference_identity' and f['source'].get('doc_id')==record.get('source_doc_id') and f['source'].get('batch_no')==batch]
                if len({f['normalized_value'] for f in study})==1:identity=study[0]['raw_value']
            docs={f['source'].get('doc_id') for f in ev if f['field']=='reference_batch' and f['normalized_value']==normalize(batch)}
            proof=[f for f in ev if f['source'].get('doc_id') in docs]
            names={f['normalized_value'] for f in proof if f['field']=='reference_identity'}
            valid=any(f['field']=='reference_validity' for f in proof)
            status='证据不足' if not identity or not names or not valid else ('通过' if names=={normalize(identity)} else '发现问题')
            checks.append(check('参比对应',status,f'批号 {batch}；研究参比 {identity or "身份未明确"}；证明参比 {sorted(names)}；'+('缺少身份/批次/效期对应证据' if status=='证据不足' else '逐项核对名称、批次和效期证明'),[record]+proof))
        return [check('B 参比制剂有效期证明',combined(checks),'；'.join(dict.fromkeys(c['reason'] for c in checks)),[f for c in checks for f in c['evidence']])]
