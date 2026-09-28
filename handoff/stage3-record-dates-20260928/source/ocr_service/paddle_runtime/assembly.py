"""由有序区域生成正文；首次解析和有效内容修订共用，无模型/数据库依赖。"""
from copy import deepcopy
import re

LABELS = ('生产地址和生产范围','统一社会信用代码','社会信用代码','许可证编号','企业名称',
          '法定代表人','企业负责人','质量负责人','质量受权人','生产负责人','经营范围',
          '生产范围','生产地址','注册地址','注册资本','成立日期','有效期至','发证机关',
          '登记机关','企业类型','分类码','名称','类型','住所')


def area(b):
    return max(0,b[2]-b[0])*max(0,b[3]-b[1])


def coverage(a, b):
    return max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))/max(area(a),1e-8)


def bounds(items):
    return [min(b[0] for b in items),min(b[1] for b in items),max(b[2] for b in items),max(b[3] for b in items)]


def field(text):
    # 只识别显式标签，不补写缺失标签，不推断具体企业/页码。
    for label in LABELS:
        match = re.match(r'^' + r'\s*'.join(map(re.escape,label)) + r'\s*[:：]?\s*', text)
        if match:
            return label, text[match.end():]
    return None, None


def region_text(lines):
    """仅区域内部按行聚合；跨区域文字永不因同高而拼接。"""
    rows = []
    for line in sorted(lines,key=lambda l: (l['bbox'][1],l['bbox'][0])):
        b=line['bbox']
        if (not rows or line.get('position_kind')=='review_region_not_glyph' or
            abs((b[1]+b[3])/2-sum(rows[-1][0]['bbox'][1::2])/2) > min(b[3]-b[1], rows[-1][0]['bbox'][3]-rows[-1][0]['bbox'][1])*.55):
            rows.append([])
        rows[-1].append(line)
    texts=[]
    for row in rows:
        row.sort(key=lambda l:l['bbox'][0])
        text=''
        for line in row:
            v=line.get('text','')
            # 两段拉丁单词之间保留空格；中文续行不插入排版空白。
            if text and v and re.search(r'[A-Za-z]$',text) and re.match(r'[A-Za-z]',v):text+=' '
            text+=v
        texts.append(text)
    paragraphs=[]
    for text in texts:
        label,value=field(text)
        if label:
            paragraphs.append(label+'：'+value)
        elif paragraphs:
            paragraphs[-1]+= (' ' if re.search(r'[A-Za-z]$',paragraphs[-1]) and re.match(r'[A-Za-z]',text) else '')+text
        else: paragraphs.append(text)
    return '\n'.join(paragraphs)


def assemble_chunk(chunk, effective_revision=False, preserve_objects=False):
    """保留原始集合，更新可读投影。已有区域顺序在修订前后保持稳定。"""
    if preserve_objects and chunk.get('readable_elements'):
        elements=deepcopy(chunk['readable_elements'])
        lines=chunk.get('lines') or chunk.get('words') or []
        for element in elements:
            if element['kind']=='table':
                ti=element.get('table_index')
                if type(ti) is int and ti<len(chunk.get('tables',[])):element['text']=table_text(chunk['tables'][ti])
                continue
            original_ids=set(element.get('source_span_ids') or [])
            replacements=[l for l in lines if l.get('manual_target_key') and
                          set(l.get('source_span_ids') or []).issubset(original_ids)]
            if len(replacements)==1:
                line=replacements[0];element['text']=line['text']
                element['label'],element['value']=field(line['text'])
                element['kind']='field' if element['label'] else 'paragraph'
                element['source_lines']=[deepcopy(line)]
                element['source_span_ids']=list(line.get('source_span_ids') or [])
                element['source_polygons']=[deepcopy(l.get('polygon')) for l in line.get('original_source_lines',[])]
                element['content_state']=(line.get('manual_confirmation') or {}).get('content_state')
        chunk['readable_elements']=elements
        chunk['text']='\n\n'.join(e['text'] for e in elements if e['text'])
        chunk['effective_text']=chunk['text']
        if effective_revision:
            chunk.setdefault('original_raw_text',chunk.get('raw_text',''));chunk['raw_text']=chunk['text']
        return chunk
    regions=deepcopy(chunk.get('layout_regions') or [])
    lines=chunk.get('lines') or chunk.get('words') or []
    tables=chunk.get('tables') or []
    if not lines and tables:
        # 无坐标的 Word/历史逻辑表沿用原文中的表格位置，保留表前表后正文。
        elements=[];table_index=0;in_table=False
        for text in str(chunk.get('text') or chunk.get('raw_text') or '').splitlines():
            if text.lstrip().startswith('|'):
                if not in_table and table_index<len(tables):
                    elements.append(dict(kind='table',region_id=f'table:{table_index}',table_index=table_index,
                                         text=table_text(tables[table_index]),bbox_pdf=tables[table_index].get('bbox_pdf',[])))
                    table_index+=1
                in_table=True
            else:
                in_table=False
                if text.strip():elements.append(dict(kind='paragraph',region_id=f'logical:{len(elements)}',text=text,bbox_pdf=[]))
        for ti in range(table_index,len(tables)):
            elements.append(dict(kind='table',region_id=f'table:{ti}',table_index=ti,text=table_text(tables[ti]),bbox_pdf=tables[ti].get('bbox_pdf',[])))
        chunk['readable_elements']=elements;chunk['assembly_issues']=[]
        chunk['text']='\n\n'.join(e['text'] for e in elements)
        chunk['effective_text']=chunk['text']
        if effective_revision:
            chunk.setdefault('original_raw_text',chunk.get('raw_text',''));chunk['raw_text']=chunk['text']
        return chunk
    has_layout=bool(regions)
    if not regions:
        # 旧资料没有模型版面时沿用已有对象序列，绝不重新全页 y/x 排序。
        regions=[dict(region_id=f'legacy:{i}',order=i,label='text',bbox_pdf=l['bbox'])
                 for i,l in enumerate(lines) if l.get('bbox') and l.get('table_index') is None]
    regions.sort(key=lambda r: (r.get('order') is None,r.get('order') or 0))
    groups={r['region_id']:[] for r in regions}; unassigned=[]; issues=[]
    for i,line in enumerate(lines):
        b=line.get('bbox')
        if not b:continue
        if line.get('table_index') is not None or any(coverage(b,t.get('bbox_pdf', [0,0,0,0]))>.98 for t in tables):continue
        parents=set(line.get('completion_of_span_ids') or [])
        completed=[rid for rid,members in groups.items() if parents and parents.issubset({l.get('source_span_id') for l in members})]
        if len(completed)==1:
            groups[completed[0]].append(line);continue
        candidates=sorted(((coverage(b,r['bbox_pdf']),-area(r['bbox_pdf']),r['region_id']) for r in regions),reverse=True)
        if candidates and candidates[0][0]>=.5:
            chosen=candidates[0][2];groups[chosen].append(line)
        else:
            unassigned.append((i,line))
    elements=[]; used_tables=set()
    for region in regions:
        rid=region['region_id'];members=groups[rid]
        linked=[(i,t) for i,t in enumerate(tables) if i not in used_tables and t.get('bbox_pdf') and
                (coverage(t['bbox_pdf'],region['bbox_pdf'])>.7 or coverage(region['bbox_pdf'],t['bbox_pdf'])>.9)]
        if linked and region.get('label')=='table':
            for ti,t in linked:
                elements.append(dict(kind='table',region_id=rid,table_index=ti,bbox_pdf=t['bbox_pdf'],text=table_text(t)))
                used_tables.add(ti)
        if members:
            body=[l for l in members if not l.get('seal_verification')]
            seals=[l for l in members if l.get('seal_verification')]
            if body:
                text=region_text(body) if has_layout else '\n'.join(l['text'] for l in body)
                label,value=field(text)
                elements.append(dict(kind='field' if label else 'paragraph',region_id=rid,label=label,value=value,
                    layout_label=region.get('label'),bbox_pdf=bounds([l['bbox'] for l in body]),text=text,
                    ordering_bbox_pdf=bounds([l.get('ordering_bbox',l['bbox']) for l in body]),
                    source_span_ids=[l.get('source_span_id') for l in body],source_lines=deepcopy(body)))
            for line in seals:
                elements.append(dict(kind='field',region_id=rid+':seal:'+line['source_span_id'],label='印章文字',value=line['text'],
                    layout_label='seal',bbox_pdf=line['bbox'],text='印章文字：'+line['text'],
                    source_span_ids=line['source_span_ids'],source_lines=[deepcopy(line)]))
        elif has_layout and region.get('label') in ('text','doc_title','paragraph_title') and not linked:
            issues.append(dict(code='ocr_coverage',stage='layout_coverage',reason='版面检测到文字区域，但现有 OCR 行未覆盖；尚不能证明原件为空',
                bbox_pdf=region['bbox_pdf'],region_id=rid,layout_evidence=deepcopy(region)))
    for ti,t in enumerate(tables):
        if ti not in used_tables:
            elements.append(dict(kind='table',region_id=f'table:{ti}',table_index=ti,bbox_pdf=t.get('bbox_pdf',[]),text=table_text(t)))
    # 残余行建立局部区域，参与同栏邻接顺序。模型漏框不是页尾指令。
    residual=[]
    for i,line in sorted(unassigned,key=lambda pair:(pair[1]['bbox'][1],pair[1]['bbox'][0])):
        b=line['bbox'];h=max(1,b[3]-b[1]);label,_=field(line.get('text',''))
        candidates=[e for e in residual if not label and
            abs(e['bbox_pdf'][0]-b[0])<h*.8 and
            -.3*h<=b[1]-e['bbox_pdf'][3]<=1.8*h]
        if len(candidates)==1:
            e=candidates[0];e['source_lines'].append(deepcopy(line))
            e['bbox_pdf']=bounds([e['bbox_pdf'],b]);e['text']=region_text(e['source_lines'])
            e['label'],e['value']=field(e['text']);e['kind']='field' if e['label'] else 'paragraph'
        else:
            residual.append(dict(kind='field' if label else 'paragraph',region_id=f'residual:{line.get("source_span_id") or i}',
                label=label,value=field(line.get('text',''))[1],bbox_pdf=b,text=line['text'],
                layout_label='text',source_lines=[deepcopy(line)],order_evidence='local_column_neighbors'))
    for element in residual:
        b=element['bbox_pdf'];anchors=[]
        for index,e in enumerate(elements):
            eb=e.get('ordering_bbox_pdf',e.get('bbox_pdf'))
            if not eb:continue
            overlap=max(0,min(b[2],eb[2])-max(b[0],eb[0]))/max(1,min(b[2]-b[0],eb[2]-eb[0]))
            if overlap<.65:continue
            gap=max(eb[1]-b[3],b[1]-eb[3],0)
            if gap>max(50,4*(b[3]-b[1])):continue
            # 左边界同列优先，但值列续行也可依其上方完整字段定位。
            distance=gap+min(abs(b[0]-eb[0])*.05,10)
            anchors.append((distance,index+(sum(b[1::2])>=sum(eb[1::2]))))
        if anchors:
            _,index=min(anchors);elements.insert(index,element)
        else:
            # 邻接距离不足时，仍可依同栏最近区域插入，但保留顺序未决证据。
            same_column=[(abs((eb[1]+eb[3]-b[1]-b[3])/2),i,e)
                         for i,e in enumerate(elements) for eb in [e.get('ordering_bbox_pdf',e.get('bbox_pdf'))] if eb and
                         min(b[2],eb[2])>max(b[0],eb[0])]
            if same_column:
                _,i,neighbor=min(same_column,key=lambda v:v[0])
                elements.insert(i+(sum(b[1::2])>=sum(neighbor.get('ordering_bbox_pdf',neighbor['bbox_pdf'])[1::2])),element)
            else:elements.append(element)
            if has_layout:
                issues.append(dict(code='text_assembly_unresolved',stage='layout_assignment',
                    reason='残余文字缺少可靠同栏邻接区域；保留原行和位置待核对',
                    bbox_pdf=b,text=element['text'],source_span_ids=[l.get('source_span_id') for l in element['source_lines']]))
    # 残余续行也可归回标签残缺的完整区域。只保留原文，不猜补字段名。
    residual_removed=set()
    for element in elements:
        if not element['region_id'].startswith('residual:') or field(element['text'])[0]:continue
        b=element['bbox_pdf'];h=max(1,b[3]-b[1]);choices=[]
        for previous in elements:
            if (previous is element or previous['kind']!='paragraph' or not previous.get('source_lines')
                    or previous['region_id'].startswith('residual:') or len(previous['text'])<12):continue
            pb=previous['bbox_pdf'];ph=max(l['bbox'][3]-l['bbox'][1] for l in previous['source_lines'])
            if not (-.2*ph<=b[1]-pb[3]<=.8*ph and pb[0]+ph<b[0]<pb[2] and b[2]<=pb[2]+ph):continue
            if any(e is not previous and e is not element and e['kind']=='field' and
                   pb[3]<=e['bbox_pdf'][1]<b[1] and e['bbox_pdf'][0]<=b[0]<=e['bbox_pdf'][2] for e in elements):continue
            choices.append(previous)
        if len(choices)==1:
            previous=choices[0];previous['source_lines'].extend(element['source_lines'])
            previous['text']=region_text(previous['source_lines']);previous['bbox_pdf']=bounds([previous['bbox_pdf'],b])
            previous.setdefault('continuation_region_ids',[]).append(element['region_id']);residual_removed.add(element['region_id'])
    elements=[e for e in elements if e['region_id'] not in residual_removed]
    # 字段的值列可越过左侧下一标签的高度；只按实际值列及邻接行判断。
    removed=set()
    for element in elements:
        if element['kind']!='paragraph' or not element.get('source_lines'):continue
        b=element['bbox_pdf'];h=max(1,b[3]-b[1]);candidates=[]
        for previous in elements:
            if previous['kind']!='field':continue
            if (re.fullmatch(r'\d{4}年\d{1,2}月\d{1,2}日',re.sub(r'\s+','',element['text']))
                    and previous.get('label') not in ('成立日期','有效期至')):continue
            pb=previous['bbox_pdf'];ph=max(l['bbox'][3]-l['bbox'][1] for l in previous.get('source_lines',[]) or [{'bbox':pb}])
            gap=b[1]-pb[3]
            if not (-.25*ph<=gap<=1.8*ph):continue
            if b[0]<pb[0]-max(3,.35*ph):continue
            # 已有值的续行必须处于值列，标签空值则允许其下方独立值块。
            if previous.get('value'):
                if previous.get('label') not in ('住所','注册地址','生产地址','经营范围','生产范围','生产地址和生产范围'):continue
                first=min(previous['source_lines'],key=lambda l:l['bbox'][1]);fb=first['bbox']
                prefix=len(previous.get('label') or '')+1
                value_x=fb[0]+(fb[2]-fb[0])*prefix/max(prefix+1,len(first.get('text','')))
                if b[0]<value_x-max(4,ph*.5) or b[0]>pb[2]+ph:continue
            elif previous.get('label') in ('登记机关','发证机关') and not re.search('[\u4e00-\u9fff]',element['text']):continue
            elif abs(b[0]-pb[0])>max(pb[2]-pb[0]+ph,3*ph):continue
            # 中间出现同值列的新字段则停止；左侧标签不截断较高的右侧续行。
            blockers=[e for e in elements if e is not previous and e['kind']=='field' and
                sum(pb[1::2])/2<sum(e['bbox_pdf'][1::2])/2<sum(b[1::2])/2 and
                abs(e['bbox_pdf'][0]-pb[0])<ph]
            if blockers:continue
            candidates.append((abs(gap),previous))
        candidates.sort(key=lambda p:p[0])
        if candidates and (len(candidates)==1 or candidates[1][0]-candidates[0][0]>h*.3):
            previous=candidates[0][1]
            previous['value']=(previous.get('value') or '')+element['text']
            previous['text']=previous['label']+'：'+previous['value']
            previous['source_lines'].extend(element['source_lines'])
            previous['bbox_pdf']=bounds([previous['bbox_pdf'],b])
            previous.setdefault('continuation_region_ids',[]).append(element['region_id'])
            removed.add(element['region_id'])
    joined=[e for e in elements if e['region_id'] not in removed]
    for element in joined:
        if 'source_lines' in element:
            element['source_span_ids']=list(dict.fromkeys(sid for l in element['source_lines'] for sid in (l.get('source_span_ids') or [l.get('source_span_id')]) if sid))
            element['source_polygons']=[deepcopy(l.get('polygon')) for l in element['source_lines']]
            seals=[l for l in element['source_lines'] if l.get('seal_verification')]
            if seals:
                element['verified_seal_segments']=[dict(text=l['text'],source_span_ids=l['source_span_ids'],
                    polygon_page=deepcopy(l['polygon']),verification=deepcopy(l['seal_verification'])) for l in seals]
                if len(seals)==len(element['source_lines']):
                    value=''.join(l['text'] for l in seals)
                    element.update(kind='field',label='印章文字',value=value,text='印章文字：'+value)
    chunk['readable_elements']=joined
    chunk['assembly_issues']=issues
    if chunk.get('applied_seal_adjudications'):
        from seal_review import attach_authority_values
        attach_authority_values(chunk)
        chunk['resolved_assembly_issues']=[e for e in issues if (e.get('stage')=='layout_assignment' and
            any(set(e.get('source_span_ids',[]))==set(r.get('field_source_span_ids',[]))
                for r in chunk.get('authority_region_associations',[]) if r.get('value_verified')))]
        chunk['assembly_issues']=[e for e in issues if e not in chunk['resolved_assembly_issues']]
    chunk['text']='\n\n'.join(e['text'] for e in joined if e['text'])
    chunk['effective_text']=chunk['text']
    if effective_revision:
        chunk.setdefault('original_raw_text',chunk.get('raw_text',''))
        chunk['raw_text']=chunk['text']
    return chunk


def table_text(table):
    rows=table.get('raw_rows') or table.get('rows') or []
    if rows:
        return '\n'.join('| '+' | '.join(str(v).replace('|','\\|').replace('\n','<br>') for v in row)+' |' for row in rows)
    return table.get('markdown','')
