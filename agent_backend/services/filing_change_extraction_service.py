"""申报事实只来自选入原件；一个值对应一条事实，不用文件名填值。"""
import re
import unicodedata
from agent.agent_backend.services.filing_numeric_revision import numeric_check_confirmed
from itertools import combinations
from typing import Any, Dict, List

FIELD_LABELS = {
 'drug_name': ['药品通用名称','通用名称','药品名称','样品名称'],
 'approval_no': ['批准文号','药品批准文号','注册证号'],
 'dosage_form': ['剂型'], 'specification': ['含量规格','规格'],
 'fill_volume': ['包装装量','装量'],
 'packaging': ['包装规格','包装','包装材质'],
 'holder': ['上市许可持有人','持有人'], 'manufacturer': ['生产企业','生产单位'],
 'company_name': ['企业名称','主体名称'], 'applicant': ['申请人名称'],
 'validity_period': ['药品有效期','有效期'], 'storage_condition': ['贮藏条件','贮藏'],
 'license_no': ['许可证编号','许可证号'], 'license_valid_until': ['证照有效期','有效期至'],
 'registered_address': ['注册地址','住所'], 'production_address': ['生产地址'],
 'production_scope': ['生产范围','生产地址和生产范围'], 'standard_no': ['标准编号','标准号','质量标准'],
 'standard_version': ['标准版本','版本号'], 'effective_date': ['生效日期','实施日期'],
 'revision_info': ['修订说明','修订日期','修改日期'], 'test_method': ['检验方法','检测方法'],
 'test_items': ['检测项目','检验项目','考察项目'], 'limit': ['可接受标准','限度','评价标准'],
 'result_text': ['检验结果','检测结果'],
 'report_no': ['报告编号','报告单编号','检验报告编号'], 'batch_no': ['样品批号','批号'],
 'inspection_date': ['检验日期','检验时间','报告时间'], 'test_standard': ['检验依据','依据标准','检测标准'],
 'product_role': ['样品角色','样品信息'], 'condition': ['考察条件','试验条件'],
 'time_point': ['检测时间点','时间点','考察时间'], 'orientation': ['放置方向'],
 'batch_size': ['批量','生产规模'], 'prescription': ['处方'], 'process': ['生产工艺'],
 'critical_parameters': ['关键工艺参数'], 'commercial_scale': ['批准批量','商业化规模'],
 'approved_prescription': ['批准处方'], 'approved_process': ['批准生产工艺'],
 'approved_parameters': ['批准关键工艺参数'], 'approved_batches':['批准适用批次'], 'impact_data':['质量影响研究数据'], 'impact_basis':['质量影响研究依据'], 'research_applicability':['研究适用性'], 'compatibility_basis':['相容性研究依据'], 'migration_data':['迁移试验数据'], 'adsorption_data':['吸附试验数据'], 'impact_assessment': ['质量影响评估'],
 'representativeness': ['代表性论证'], 'batch_sequence': ['生产批次顺序'],
 'method_validation': ['方法确认结论','方法验证结论'], 'bridge_conclusion': ['桥接结论'],
 'bridge_data': ['桥接数据'], 'bridge_versions': ['桥接标准版本'],
 'packaging_registration_status': ['包材登记证明结论'], 'packaging_registration': ['包材登记号'], 'compatibility': ['相容性结论'],
 'compatibility_data': ['相容性研究数据'], 'migration': ['迁移试验结论'], 'adsorption': ['吸附试验结论'],
 'commitment': ['持续稳定性考察承诺'], 'abnormal_reporting': ['异常上报承诺'],
 'reference_identity': ['参比制剂名称'], 'reference_batch': ['参比制剂批号'],
 'reference_validity': ['参比制剂有效期'], 'chromatogram_id': ['图谱编号'],
 'study_object': ['研究对象'], 'planned_batches': ['研究批次'], 'planned_conditions': ['研究条件'],
 'planned_specifications': ['研究规格'], 'planned_packaging': ['研究包装'],
 'planned_times': ['计划时间点','取样时间点'], 'planned_items': ['计划检测项目','长期试验考察项目'],
 'planned_orientation':['研究放置方向','计划放置方向'], 'planned_standard': ['研究评价标准'], 'significant_criteria': ['显著变化判定标准'],
 'condition_justification': ['贮藏条件匹配依据'],
}
COMPARISON_FIELDS = ['drug_name','approval_no','dosage_form','specification','fill_volume','packaging','holder','manufacturer','validity_period','storage_condition']
REQUIRED = {
 'approval': ['drug_name','approval_no','dosage_form','specification','holder','validity_period','storage_condition'],
 'license': ['company_name','license_no','production_address','production_scope','license_valid_until'],
 'standard': ['standard_no','test_items','limit','test_method'],
 'label': ['drug_name','specification','packaging','manufacturer','validity_period','storage_condition'],
 'inspection': ['report_no','drug_name','batch_no','inspection_date','test_standard','test_items'],
}
CATEGORY = {'approval':'1','license':'2','standard':'4','label':'4','inspection':'3','stability':'5.6'}


def normalize(value):
    # 不删否定、范围、单位、条件；仅统一 Unicode 和无语义的空格。
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value or ''))).replace('～','~')


def split_specification(facts):
    """只拆显式每容器装量，含量计量基准和完整原文继续保留。"""
    result=[]
    for fact in facts:
        f={**fact}
        if f['field'] in ('specification','packaging'):
            raw=f['raw_value']; value=normalize(raw)
            volumes=list(re.finditer(r'每(?:瓶|支|袋|罐)\s*\d+(?:\.\d+)?\s*(?:ml|mL|毫升|g|克)',value))
            if f['field']=='specification' and volumes:
                content=value
                for match in reversed(volumes):content=content[:match.start()]+content[match.end():]
                content=re.sub(r'[;；,，]+(?=\))','',content)
                f['normalized_value']=content.replace('()','')
                f['normalization_reason']='仅将明确每容器装量拆入包装装量；含量计量基准保留'
            for match in volumes:
                result.append({**fact,'field':'fill_volume','label':'包装装量','raw_value':match.group(),'original_expression':raw,'normalized_value':match.group().replace('mL','ml').replace('毫升','ml').replace('克','g'),'derived_from':fact['field']})
        result.append(f)
    return result


def month_list(text):
    values=[]
    for match in re.finditer(r'(\d+(?:\.\d+)?(?:\s*[、,，]\s*\d+(?:\.\d+)?)*)\s*(?:个)?月',text):
        values.extend(float(v) for v in re.findall(r'\d+(?:\.\d+)?',match.group(1)))
    return sorted(set(values))


def planned_items(text):
    # 括号内的考察月份属于项目限定，不能按顿号拆成项目。
    parts=re.split(r'[、;；](?![^（(]*[）)])',text)
    result=[]
    for part in parts:
        name=re.split(r'[（(]',part)[0].strip()
        if name and not re.fullmatch(r'[\d、,，.]+(?:个)?月.*',name):
            result.append({'name':name,'time_points':month_list(part) if re.search(r'[（(]',part) else [],'raw_value':part})
    return result


def is_submission(row):
    extracted = row.get('extracted_json') or {}
    return row.get('review_enabled', True) and row.get('material_role') != 'reference' and extracted.get('material_role') != 'reference'


def source_lines(chunks):
    for ci, chunk in enumerate(chunks or [], 1):
        base = {'chunk': ci, 'page': chunk.get('page'), 'section': ' > '.join(chunk.get('section_path') or [])}
        for li, line in enumerate(str(chunk.get('text') or '').splitlines(), 1):
            if line.strip() and not line.strip().startswith('|'):
                matching = [entry for entry in chunk.get('lines', []) if str(entry.get('text', '')).strip() == line.strip()]
                checks = []
                for entry in matching:
                    box = entry.get('bbox')
                    if not box:
                        continue
                    for word in chunk.get('words', []):
                        wb = word.get('bbox')
                        if (wb and box[0] <= (wb[0]+wb[2])/2 <= box[2] and box[1] <= (wb[1]+wb[3])/2 <= box[3]
                                and word.get('numeric_verification')):
                            checks.append(word['numeric_verification'])
                geometry = {k: matching[0][k] for k in ('bbox', 'block_id', 'column_id') if len(matching) == 1 and k in matching[0]}
                yield line.strip(), {**base, **geometry, 'line': li, **({'numeric_verification': checks} if checks else {})}
        for ti, table in enumerate(chunk.get('tables') or [], 1):
            rows = table.get('raw_rows') or ([table.get('headers') or []] + table.get('rows', []) if table.get('rows') else [])
            # 公共PDF表格rows已含表头，不能再插入空表头造成证据行号偏移。
            if table.get('cells') and table.get('columns') and table.get('rows') and table['rows'][0] == table['columns']:
                rows = table['rows']
            if not rows:
                rows = [table.get('columns') or table.get('headers') or []] + (table.get('data') or [])
            if not any(rows):
                rows = [[c.strip() for c in x.strip().strip('|').split('|')] for x in str(table.get('markdown') or '').splitlines() if x.strip().startswith('|')]
            headers = rows[0] if rows else []
            label_set={v for labels in FIELD_LABELS.values() for v in labels}
            is_header=sum(str(c) in label_set for c in headers)>=2 and not any(re.match(r'^(?:\d|B\d|国药|YBH)',str(c)) for c in headers)
            metadata=table.get('structured_data') or {}
            for ri, row in enumerate(rows, 1):
                if not isinstance(row, list): continue
                for col, cell in enumerate(row, 1):
                    text = str(cell or '').strip()
                    loc = {**base, 'table_type':table.get('table_type',''), 'table': table.get('table_index', ti), 'row': ri, 'cell': col, 'page': table.get('page', base['page'])}
                    def cell_evidence(column):
                        return [check for source_cell in table.get('cells', [])
                                if source_cell.get('row', -1) <= ri-1 < source_cell.get('row', -1)+source_cell.get('rowspan', 1)
                                and source_cell.get('column', -1) <= column-1 < source_cell.get('column', -1)+source_cell.get('colspan', 1)
                                for check in source_cell.get('numeric_verification', [])]
                    checks = cell_evidence(col)
                    if checks:
                        loc['numeric_verification'] = checks
                    if not text: continue
                    if col>1 and str(row[col-2])==text and (':' in text or '：' in text or text in label_set): continue
                    row_role = '参比制剂' if any('参比' in str(c) for c in row[:1]) else ('自制制剂' if any('自制' in str(c) for c in row[:1]) else metadata.get('product_role',''))
                    loc['product_role']=row_role
                    loc['batch_no']=metadata.get('batch_no','')
                    if is_header and '批号' in headers and ri>1:
                        bi=headers.index('批号')
                        if bi<len(row) and str(row[bi]) not in label_set:loc['batch_no']=str(row[bi])
                    yield text, loc
                    if not (ri==1 and is_header) and col < len(row) and text in label_set and str(row[col]) not in label_set:
                        yield text + '：' + str(row[col] or ''), {**loc, 'cell': col + 1,
                            'numeric_verification': checks + cell_evidence(col+1)}
                    if is_header and ri > 1 and col <= len(headers) and str(headers[col-1]) in label_set and text not in label_set:
                        yield str(headers[col-1]) + '：' + text, loc


def extract_document(file_name, chunks):
    lines = list(source_lines(chunks))
    text = '\n'.join(t for t, _ in lines)
    title = '\n'.join(t for t, _ in lines[:8])
    role = 'reference' if re.search(r'指导原则|技术指导原则|管理办法|审评要点', title) and not re.search(r'本品|本次|我司|研究报告|延长有效期资料', title) else 'submission'
    types = []
    for kind, pattern in [('approval',r'药品注册证书|药品批准证明|药品补充申请批准|批准文号'),('license',r'药品生产许可证|营业执照|许可证编号'),('standard',r'药品注册标准|质量标准|标准编号'),('label',r'说明书|标签'),('inspection',r'检验报告|检验报告单|报告编号'),('stability',r'稳定性|长期试验')]:
        if re.search(pattern, title): types.append(kind)
    facts = []
    labels = sorted([(label, key) for key, values in FIELD_LABELS.items() for label in values], key=lambda x:-len(x[0]))
    all_labels = '|'.join(re.escape(x[0]) for x in labels)
    pattern = re.compile(r'(?:【(?P<bracket>'+all_labels+r')】\s*[:：]?|(?<![\w\u4e00-\u9fff])(?P<label>'+all_labels+r')\s*[:：])\s*')
    component=''
    from agent.agent_backend.services.filing_scope_continuation import scope_lines, scope_entries
    field_lines = scope_lines(lines, pattern, FIELD_LABELS)
    for line, loc in field_lines:
        if re.fullmatch(r'(?:修订后|药品)?说明书(?:样稿)?[：:]?',line):component='instructions'
        elif re.fullmatch(r'(?:药品)?标签(?:样稿)?[：:]?',line):component='package_label'
        loc={**loc,'material_component':component}
        matches = list(pattern.finditer(line))
        for mi, match in enumerate(matches):
            label = match.group('label') or match.group('bracket')
            field = next(k for l,k in labels if l == label)
            raw = line[match.end():matches[mi+1].start() if mi+1<len(matches) else len(line)].strip(' ；;')
            if match.start()>0 and line[match.start()-1] in '（(':
                raw=re.split(r'[）)]',raw,maxsplit=1)[0].strip()
            if field == 'validity_period' and 'license' in types: field = 'license_valid_until'
            context = 'proposed' if re.search(r'拟|修订后|变更后', line[:match.start()]+title) else ('approved' if 'approval' in types or re.search(r'原批准|变更前',line[:match.start()]) else 'current')
            status = 'blank' if raw in ('','/','—') else ('not_applicable' if re.match(r'^不适用[（(:：].+',raw) else 'available')
            facts.append({'field':field,'label':label,'raw_value':raw,'normalized_value':normalize(raw).replace('个月','月') if field=='validity_period' else normalize(raw),'status':status,'context':context,'source':{'file_name':file_name,**loc}})
    for fact in facts:
        if fact['field'] == 'production_scope':
            fact['scope_entries'] = scope_entries(fact['raw_value'], fact['source'])
            if fact['label'] == '生产地址和生产范围':
                fact['attribution_status'] = 'combined_address_scope_requires_review'
                fact['joint_block'] = {'raw_value':fact['raw_value'],'source':fact['source'],
                    'unresolved':['subject_address_scope_not_separated']+fact['source'].get('internal_unresolved',[])}
                for entry in fact['scope_entries']:
                    entry['unresolved'].append('combined_address_scope_requires_review')
            origin = fact['source']
            addresses = [f for f in facts if f['field'] == 'production_address'
                and all(f['source'].get(k) == origin.get(k) for k in ('chunk','page','table','row','block_id','column_id'))
                and (origin.get('table') is not None or f['source'].get('line', 0) < origin.get('line', 0))]
            if origin.get('table') is None and addresses:
                addresses = [max(addresses, key=lambda f: f['source'].get('line', 0))]
            fact['production_address_evidence'] = [{'raw_value': f['raw_value'], 'source': f['source']} for f in addresses]
            for entry in fact['scope_entries']:
                entry['production_address_evidence'] = fact['production_address_evidence']
    # 标准编号自身的明确格式也是原文，不从文件名或邻字段借值。
    for line, loc in lines:
        for value in re.findall(r'\b(?:YBH\d{6,}|STP-[A-Za-z0-9,，.\-]+)',line):
            facts.append({'field':'standard_no','label':'标准编号','raw_value':value,'normalized_value':normalize(value),'status':'available','context':'current','source':{'file_name':file_name,**loc}})
    # 原文明确的取样计划独立保存，不把已测结果列反推成必检清单。
    for line, loc in lines:
        if '取样检测' not in line: continue
        patterns={'planned_times':r'分别于(.+?)取样检测','planned_conditions':r'在([^。]+?)条件下','planned_batches':r'自制[^（(]*[（(]批号[：:]([^）)]+)','study_object':r'对(.+?)在'}
        for field, expression in patterns.items():
            match=re.search(expression,line)
            if match:
                value=match.group(1).strip()
                facts.append({'field':field,'label':FIELD_LABELS[field][0],'raw_value':value,'normalized_value':normalize(value),'status':'available','context':'current','source':{'file_name':file_name,**loc}})
    for fact in facts:
        checks = fact.get('source', {}).get('numeric_verification', [])
        if any(not numeric_check_confirmed(check) for check in checks):
            fact.update(status='manual_review', reason_code='numeric_uncertain')
    batches={f['raw_value'] for f in facts if f['field']=='batch_no' and f['status']=='available'}
    if len(batches)==1:
        for f in facts:f['source'].setdefault('batch_no',next(iter(batches)))
    facts=split_specification(facts)
    seen = set(); unique=[]
    for f in facts:
        key=(f['field'],f['raw_value'],str(f['source']))
        if key not in seen: unique.append(f);seen.add(key)
    result={'facts':unique,'document_types':types,'label_kinds':[k for k,pat in [('instructions',r'^(?:修订后|药品)?说明书(?:样稿)?[：:]?$'),('package_label',r'^(?:药品)?标签(?:样稿)?[：:]?$')] if any(re.search(pat,t.strip()) for t,_ in lines)],'material_role':role,'evidence_images':[{'chunk':i+1,'images':c.get('images',[])} for i,c in enumerate(chunks) if c.get('images')], 'source_statements':[{'text':t,'source':{'file_name':file_name,**loc}} for t,loc in lines]}
    for key in FIELD_LABELS:
        values=list(dict.fromkeys(f['raw_value'] for f in unique if f['field']==key and f['status']=='available'))
        result[key]=values[0] if len(values)==1 else (values if values else '')
    result['study_plans'] = plans_from_facts(unique)
    result['field_states']={key:('conflict' if len({f['normalized_value'] for f in unique if f['field']==key and f['status']=='available'})>1 else next((f['status'] for f in unique if f['field']==key),'not_extracted')) for key in FIELD_LABELS}
    return result


def collect_facts(submissions):
    from agent.agent_backend.services.filing_parse_readiness import effective_parse_ready
    facts=[]
    for s in submissions or []:
        if not is_submission(s): continue
        attempt=s.get('latest_attempt') or {}
        for f in (s.get('extracted_json') or {}).get('facts',[]):
            f={**f,'source':{**f.get('source',{}),'doc_id':s.get('doc_id'),'file_name':s.get('file_name')}}
            f['availability']='current' if effective_parse_ready(s) else 'previous_or_partial'
            if s.get('parse_resolution'):
                f['parse_resolution'] = dict(s['parse_resolution'])
            facts.append(f)
    return facts


def compare_facts(facts):
    items=[]
    for field in COMPARISON_FIELDS:
        values=[f for f in facts if f['field']==field and f['status']=='available']
        unique={}
        for value in values:
            src=value['source']
            key=(value['normalized_value'],value.get('context'),value.get('availability'),src.get('doc_id'),src.get('table'),src.get('batch_no'),src.get('product_role'))
            unique.setdefault(key,value)
        values=list(unique.values())
        pairs=[(a,b) for a,b in combinations(values,2) if (a['source'].get('doc_id'),a['source'].get('table'))!=(b['source'].get('doc_id'),b['source'].get('table'))]
        if not pairs:
            items.append({'field':field,'field_label':FIELD_LABELS[field][0],'left':values[0] if values else None,'right':None,'status':'无法核验','reason':'缺少两份材料的对应事实'})
        for a,b in pairs:
            ar,br=a['source'].get('product_role'),b['source'].get('product_role')
            if ar and br and ar!=br: continue
            if (ar=='参比制剂') != (br=='参比制剂'): continue
            ab,bb=a['source'].get('batch_no'),b['source'].get('batch_no')
            if ab and bb and ab!=bb: continue
            reason='原值经等价格式归一化后相同';status='一致'
            if a.get('availability')!='current' or b.get('availability')!='current': status='无法核验';reason='存在此前结果或不完整解析，需核对本次原件'
            elif a['normalized_value'] != b['normalized_value'] and a.get('context')!=b.get('context') and {a.get('context'),b.get('context')}=={'approved','proposed'}:
                revisions=[f for f in facts if f['field']=='revision_info' and f['status']=='available' and f['source'].get('doc_id') in (a['source'].get('doc_id'),b['source'].get('doc_id'))]
                def supports_change(revision):
                    text=normalize(revision['raw_value'])
                    if field=='validity_period':
                        endpoints=[re.search(r'\d+(?:\.\d+)?',v['raw_value']) for v in (a,b)]
                        return all(m and m.group() in text for m in endpoints) and any(x in text for x in ('延长','修订','变更'))
                    return field=='storage_condition' and all(v['normalized_value'] in text for v in (a,b))
                revisions=[r for r in revisions if supports_change(r)]
                status='前后变更' if revisions else '无法核验';reason='原批准与拟变更值，已提供修订信息' if revisions else '原批准与拟变更语境不同，缺少对应修订依据'
            elif a['normalized_value']!=b['normalized_value']:
                # 多规格不能任意笛卡尔积交叉判冲突。
                multi=any(len({f['normalized_value'] for f in values if (f['source'].get('doc_id'),f['source'].get('table'))==(x['source'].get('doc_id'),x['source'].get('table'))})>1 for x in (a,b))
                status='无法核验' if multi else '不一致';reason='多值材料缺少规格/包装逐项对应' if multi else '同字段、同角色的原文值不同'
            items.append({'field':field,'field_label':FIELD_LABELS[field][0],'left':a,'right':b,'status':status,'reason':reason})
    return items


def content_completeness(submissions):
    from agent.agent_backend.services.filing_parse_readiness import effective_parse_ready
    missing=[];issues=[]
    for row in submissions:
        if not is_submission(row): continue
        ex=row.get('extracted_json') or {};state=(row.get('latest_attempt') or {}).get('content_status') or row.get('parse_status')
        if not effective_parse_ready(row):
            issues.append({'doc_id':row.get('doc_id'),'file_name':row.get('file_name'),'status':state,'message':'文件已选入，但本次内容未可靠解析；不能视为未提交','latest_attempt':row.get('latest_attempt',{})});continue
        for kind in ex.get('document_types',[]):
            for key in REQUIRED.get(kind,[]):
                if not any(f['field']==key and f['status'] in ('available','not_applicable') for f in ex.get('facts',[])):
                    missing.append({'doc_id':row.get('doc_id'),'file_name':row.get('file_name'),'field':key,'message':f"{row.get('file_name')}：未获取{FIELD_LABELS[key][0]}，请核对可读原件内容"})
    return missing,issues


class FilingChangeExtractionService:
    def run(self, application_form, submissions, parsed_submission_map):
        scoped=[]
        for s in submissions or []:
            if not is_submission(s):continue
            chunks=(parsed_submission_map or {}).get(str(s.get('doc_id')),[])
            if chunks:
                fresh=extract_document(s.get('file_name',''),chunks)
                old=s.get('extracted_json') or {}
                s['extracted_json']={**old,**fresh}
                # 明确人工修订字段保留，自动候选另存。
                for key in old.get('manual_fields',[]): s['extracted_json'][key]=old.get(key)
            if is_submission(s):scoped.append(s)
        form=(application_form or {}).get('form_json',{})
        return {'application_form':form,'facts':collect_facts(scoped)+form_facts(form),'classified_materials':[{'doc_id':s.get('doc_id'),'file_name':s.get('file_name'),'material_category':s.get('material_category'),'automatic_types':s.get('extracted_json',{}).get('document_types',[])} for s in scoped], 'parse_warnings':content_completeness(scoped)[1], 'submissions':scoped}


def classify_table(rows, caption=''):
    text=' '.join(str(c) for row in rows for c in row)
    heading=' '.join(str(c) for row in rows[:3] for c in row)
    if '原申报' in text and '修订后' in text: return 'quality_standard_revision'
    if '报告单编号' in text and '报告时间' in text: return 'report_explanation'
    if ('考察时间' in text and '检测标准' in text and '变化' in text) or '标准版本变化' in caption: return 'standard_version_timeline'
    if '样品信息' in heading and '批号' in heading: return 'sample_source'
    if re.search(r'研究方案|试验方案|计划时间|取样计划',caption+' '+heading) and not re.search(r'检测结果|实测值|测定结果',heading): return 'study_plan'
    if re.search(r'时间|时间点',heading) and all(x in heading for x in ('指标','结果','限度')): return 'stability_result'
    if re.search(r'检测结果|实测值|测定结果',text) and re.search(r'考察项目|检测项目|检验项目|质量指标',text):return 'stability_result'
    # 常规纵向结果矩阵要求实际限度行；仅时间列不够。
    if re.search(r'考察时间|时间点|月份',heading) and any(str(row[0]).strip() in ('限度','标准','可接受标准') for row in rows if row): return 'stability_result'
    return 'generic'


def plans_from_facts(facts):
    fields={'product_role':'study_object','batch_no':'planned_batches','condition':'planned_conditions','specification':'planned_specifications','packaging':'planned_packaging','time_points':'planned_times','indicators':'planned_items','standard':'planned_standard','orientation':'planned_orientation'}
    values={key:[f for f in facts if f['field']==field and f['status']=='available'] for key,field in fields.items()}
    if not any(values.values()): return []
    # 多方案必须按同一表格行关联；散文只有单值时才能生成一个方案。
    table_rows={tuple((f['source'].get(k) for k in ('chunk','table','row'))) for fs in values.values() for f in fs if f['source'].get('table')}
    candidates=[]
    for row in table_rows:
        candidate={key:[f for f in fs if tuple(f['source'].get(k) for k in ('chunk','table','row'))==row] for key,fs in values.items()}
        if sum(bool(v) for v in candidate.values())>=4:candidates.append(candidate)
    if not candidates and all(len({f['raw_value'] for f in fs})<=1 for fs in values.values()):candidates=[values]
    plans=[]
    for candidate in candidates:
        plan={key:next((f['raw_value'] for f in fs),'') for key,fs in candidate.items()}
        plan['sources']=[f for fs in candidate.values() for f in fs]
        plan['time_points']=month_list(plan['time_points'])
        plan['item_requirements']=planned_items(plan['indicators'])
        plan['indicators']=[v['name'] for v in plan['item_requirements']]
        for batch in re.split('[、,，;；]',plan['batch_no']):
            plans.append({**plan,'batch_no':batch.strip()})
    return plans


def form_facts(form):
    mappings={'drug_name':('item_6_generic_name',None),'dosage_form':('item_11_dosage_form',None),'specification':('item_12_specification',None),'packaging':('item_14_packaging','packaging_specification'),'manufacturer':('item_31_manufacturer_info','manufacturer_name'),'applicant':('item_30_applicant_info','applicant_name'),'validity_period':('item_15_validity_period','proposed_validity_period'),'storage_condition':('item_15_validity_period','storage_condition')}
    result=[]
    for field,(key,sub) in mappings.items():
        node=form.get(key) or {};value=(node.get('sub_fields') or {}).get(sub) if sub else (node.get('value') or '、'.join(node.get('selected_values') or []))
        if not value:continue
        result.append({'field':field,'label':FIELD_LABELS[field][0],'raw_value':str(value),'normalized_value':normalize(value).replace('个月','月') if field=='validity_period' else normalize(value),'status':'available','availability':'current','context':'proposed' if field in ('validity_period','storage_condition') else 'current','source':{'doc_id':'application_form','file_name':'本次申请表','field':key,'subfield':sub,'detail':node.get('value_sources',{})},'manual_modified':node.get('manual_modified',False)})
    original=(form.get('item_23_original_approval_info') or {}).get('value')
    if original:
        for f in extract_document('申请表原批准信息',[{'text':str(original)}])['facts']:
            f.update(availability='current',context='approved')
            f['source'].update(doc_id='application_form',field='item_23_original_approval_info')
            result.append(f)
    return split_specification(result)
