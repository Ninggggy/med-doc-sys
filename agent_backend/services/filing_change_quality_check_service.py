import re
from agent.agent_backend.services.filing_change_extraction_service import collect_facts, compare_facts, normalize


def bridge_check(submissions):
    facts=collect_facts(submissions)
    values=lambda field:[f for f in facts if f['field']==field and f['status']=='available']
    standards=values('standard_no')+values('test_standard')
    version_facts=values('standard_version')
    identities=[]
    for f in values('standard_no'):
        related=[v for v in version_facts if v['source'].get('doc_id')==f['source'].get('doc_id')]
        for v in related or [None]:
            identities.append({'standard_no':f['normalized_value'],'version':v['normalized_value'] if v else '', 'source':f['source'],'version_source':v['source'] if v else None})
    versions={x['standard_no']+(' / '+x['version'] if x['version'] else '') for x in identities}
    for submission in submissions:
        versions.update(normalize(v) for v in (submission.get('extracted_json') or {}).get('standard_versions',[]))
    evidence=standards+version_facts+sum((values(k) for k in ('effective_date','revision_info','method_validation','bridge_versions','bridge_data','bridge_conclusion')),[])
    revision=' '.join(f['raw_value'] for f in values('revision_info'))
    validation=' '.join(f['raw_value'] for f in values('method_validation'))
    conclusion=' '.join(f['raw_value'] for f in values('bridge_conclusion'))
    state='证据不足';reason='缺少标准编号、适用期间、检验依据及方法确认资料'
    if re.search(r'不通过|不可比|验证失败',validation+' '+conclusion):
        state='发现问题';reason='方法确认或桥接结论明确不支持结果可比性'
    elif re.search(r'未验证|尚未|尚无|待确认',validation):
        reason='方法确认尚未完成，缺少适用的验证依据'
    elif versions and values('effective_date') and values('test_standard'):
        if len(versions)==1 and re.search(r'通过|符合',validation):
            state='通过';reason='检验依据与单一标准对应，存在生效时间和方法确认资料'
        elif len(versions)>1:
            editorial=bool(re.search(r'仅.*(?:排版|错别字|文字勘误)',revision) and re.search(r'(?:方法|限度).*不变|不涉及.*(?:方法|限度)',revision))
            method_change=bool(re.search(r'方法.*(?:修订|改变|变更)|(?:修订|变更).*方法',revision))
            if editorial:
                state='通过';reason='修订明确仅为文字/排版且不涉及方法限度变化，不要求桥接试验'
            elif method_change:
                paired=normalize(' '.join(f['raw_value'] for f in values('bridge_versions')))
                data=' '.join(f['raw_value'] for f in values('bridge_data'))
                covered=all(normalize(x['standard_no']) in paired and (not x['version'] or x['version'] in paired) for x in identities)
                if covered and re.search(r'通过|符合',validation) and len(re.findall(r'\d+(?:\.\d+)?\s*(?:%|mg|ug)',data))>=2 and re.search(r'旧法|原方法',data) and re.search(r'新法|修订后方法',data) and re.search(r'可比|通过',conclusion) and not re.search(r'不|尚|待',conclusion):
                    state='通过';reason='方法变更各版本已关联，修订、验证及新旧方法数据支持可比性'
                else:reason='修订涉及检测方法，缺少覆盖对应版本的验证或新旧方法桥接数据/结论'
            else:reason='修订影响无法确定，缺少各版本修订内容、适用期间及是否影响方法/结果可比性的依据；不按版本数量强制桥接'
    return {'status':state,'reason':reason,'versions':sorted(versions),'standard_identities':identities,'evidence':evidence}



class FilingChangeQualityCheckService:
    def run(self, submissions):
        comparisons=compare_facts(collect_facts(submissions))
        bridge=bridge_check(submissions)
        bad=[f"{x['field']}：{x['reason']}" for x in comparisons if x['status']=='不一致']
        uncertain=[f"{x['field']}：{x['reason']}" for x in comparisons if x['status']=='无法核验']
        if bridge['status']!='通过':uncertain.append(bridge['reason'])
        return {'result':'存在不一致' if bad else ('需人工确认' if uncertain else '基本符合'),'items':comparisons,'comparisons':comparisons,'standard_bridge':bridge,'non_compliance_items':bad,'risk_items':uncertain,'near_limit_items':[],'evidence':collect_facts(submissions)}
