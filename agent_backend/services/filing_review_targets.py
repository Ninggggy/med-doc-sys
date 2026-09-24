"""文档内可主动核对的真实对象；不是新增的自动阻塞清单。"""
from copy import deepcopy
from agent.agent_backend.services.filing_parse_resolution import rect, overlaps, contains

TARGET_ACTIONS = {'edit_value', 'confirm_value', 'confirm_blank', 'confirm_unfilled', 'mark_unreadable'}


def review_targets(source, kind, chunks):
    doc_id = source.get('doc_id') or source.get('original_file_id') or source.get('source_file_id')
    if not doc_id:
        return []
    result = []
    for ci, chunk in enumerate(chunks):
        base = dict(source_kind=kind, doc_id=doc_id, file_name=source.get('file_name', ''),
                    chunk_index=ci, page=chunk.get('page'), code='content_review', blocking=False)
        for ti, table in enumerate(chunk.get('tables', [])):
            if not table.get('cells'):
                for ri, row in enumerate(table.get('raw_rows') or table.get('rows') or []):
                    if not isinstance(row, list):
                        continue
                    for col, value in enumerate(row):
                        result.append({**base, 'issue_key': f'target:{doc_id}:{ci}:table:{ti}:row:{ri}:column:{col}',
                            'target_kind': 'logical_cell', 'table_index': ti, 'row': ri, 'column': col,
                            'bbox_pdf': [], 'position_kind': 'logical_row_column_no_page_coordinates',
                            'value': str(value), 'message': f'表{ti+1} 第{ri+1}行 第{col+1}列（逻辑位置）'})
            for cell_index, cell in enumerate(table.get('cells', [])):
                result.append({**base, 'issue_key': f'target:{doc_id}:{ci}:table:{ti}:cell:{cell_index}',
                    'target_kind': 'cell', 'table_index': ti, 'cell_index': cell_index,
                    'bbox_pdf': cell.get('bbox_pdf') or cell.get('source_bbox_pdf') or table.get('bbox_pdf', []),
                    'position_kind': 'cell' if cell.get('bbox_pdf') else 'table_region',
                    'value': str(cell.get('text', '')), 'message': f'表{ti+1} 第{cell.get("row",0)+1}行 第{cell.get("column",0)+1}列'})
        lines = chunk.get('lines') or chunk.get('words') or []
        for li, line in enumerate(lines):
            box = line.get('bbox')
            if not box or any(contains(rect(t['bbox_pdf']), rect(box)) for t in chunk.get('tables', []) if t.get('bbox_pdf')):
                continue
            result.append({**base, 'issue_key': f'target:{doc_id}:{ci}:line:{li}', 'target_kind': 'line',
                'line_index': li, 'bbox_pdf': list(box), 'position_kind': 'line_or_region',
                'value': str(line.get('text', '')), 'message': f'正文区域{li+1}：{str(line.get("text", ""))[:45]}'})
        if not lines and not chunk.get('tables') and str(chunk.get('text') or chunk.get('raw_text') or '').strip():
            result.append({**base, 'issue_key': f'target:{doc_id}:{ci}:chunk', 'target_kind': 'chunk',
                'bbox_pdf': chunk.get('page_bbox') or [], 'position_kind': 'document_chunk',
                'value': str(chunk.get('text') or chunk.get('raw_text')), 'message': f'原件内容块{ci+1}（无逐字坐标）'})
    return result


def review_issues(source, kind, chunks):
    from agent.agent_backend.services.filing_parse_readiness import source_issues
    return source_issues(source, kind, chunks) + review_targets(source, kind, chunks)


def manual_pending(chunks):
    return [deepcopy(x) for chunk in chunks for x in chunk.get('manual_unreadable', [])]


def target_value(chunks, target):
    chunk=chunks[target['chunk_index']]
    if target['target_kind']=='logical_cell':
        table=chunk['tables'][target['table_index']]
        return str((table.get('raw_rows') or table.get('rows'))[target['row']][target['column']])
    if target['target_kind']=='cell':return str(chunk['tables'][target['table_index']]['cells'][target['cell_index']].get('text',''))
    if target['target_kind']=='chunk':return str(chunk.get('text') or chunk.get('raw_text') or '')
    values=[l.get('text','') for l in (chunk.get('lines') or chunk.get('words') or []) if l.get('bbox')==target.get('bbox_pdf')]
    return '\n'.join(values)


def apply_targets(chunks, issues, items):
    """复用原来源/修订版本校验；只对真实对象生成独立有效副本。"""
    targets = {x['issue_key']: x for x in issues if x.get('code') == 'content_review'}
    output = deepcopy(chunks); saved=[]; resolved=set(); touched=set(); boxes=[]
    for item in items:
        target=targets.get(item.get('issue_key'))
        if target is None or item['issue_key'] in touched:
            raise ValueError('核对对象不属于当前文档或重复提交')
        touched.add(item['issue_key']); action=item.get('action'); reason=item.get('reason')
        if action not in TARGET_ACTIONS or not isinstance(reason,str) or not reason.strip() or len(reason)>2000:
            raise ValueError('请选择核对状态并填写依据')
        if item.get('source_value') != target['value']:
            raise ValueError('核对对象原值已变化，请重新读取')
        value=item.get('text', '')
        if not isinstance(value,str) or len(value)>100000:
            raise ValueError('修订内容格式无效')
        if action in ('edit_value','confirm_value'):
            if not value.strip():raise ValueError('空内容不能确认为正确；确实空白或未填写须明确选择')
            if item.get('verified_value')!=value:raise ValueError('内容已改变，请重新核对当前值后保存')
            if any(c in value for c in '?？□') and item.get('symbols_literal') is not True:
                raise ValueError('存在不确定符号；请标记无法辨认，或明确确认这些符号本就在原件中')
        elif action in ('confirm_blank','confirm_unfilled'):
            if value.strip():raise ValueError('空白或未填写状态不能同时提交正文')
        ci=target['chunk_index'];chunk=output[ci];box=target.get('bbox_pdf')
        if box:
            box=rect(box)
            if any(c==ci and overlaps(box,b) for c,b in boxes):raise ValueError('主动修订区域重叠，请分开核对完整对象')
            boxes.append((ci,box))
        state={'edit_value':'reviewed_text','confirm_value':'confirmed','confirm_blank':'explicit_blank',
               'confirm_unfilled':'unfilled','mark_unreadable':'unreadable'}[action]
        effective='' if state=='unreadable' else value
        record={k:deepcopy(item[k]) for k in ('issue_key','action','reason','source_value','text','verified_value','symbols_literal','reviewer','reviewed_at') if k in item}
        record.update(content_state=state,confirmed_value=value if state!='unreadable' else None)
        if state=='unreadable':
            chunk.setdefault('manual_unreadable',[]).append({**target,'blocking':True,'code':'manual_unreadable',
                'message':'核对人标记原件仍无法辨认，待处理','candidate_text':value})
        else:resolved.add(item['issue_key'])
        if target['target_kind']=='logical_cell':
            table=chunk['tables'][target['table_index']]
            rows=deepcopy(table.get('raw_rows') or table.get('rows'))
            rows[target['row']][target['column']]=effective
            from agent.agent_backend.services.filing_change_submission_parser import _FilingChangeDocxParser
            parser=_FilingChangeDocxParser('effective_document')
            structured=parser._build_structured_table(table.get('table_type',''),rows,table.get('caption',''))
            table.update(raw_rows=rows,rows=structured.get('rows',[]),headers=structured.get('headers',[]),
                         structured_data=structured,markdown=parser._table_to_markdown(rows))
            table.setdefault('manual_cell_states',{})[f"{target['row']}:{target['column']}"]=record
            chunk['text']=chunk['raw_text']='\n'.join([chunk.get('section_name','')]+[t.get('markdown','') for t in chunk['tables']])
        elif target['target_kind']=='cell':
            table=chunk['tables'][target['table_index']];cell=table['cells'][target['cell_index']]
            cell.update(text=effective,content_state=state,manual_confirmation=deepcopy(record))
            if not cell.get('bbox_pdf') and target['position_kind']!='cell':
                # 没有真实格内位置时不伪造字框；表格消费者使用有效格值。
                table['manual_values_without_glyph_positions']=True
            if box and target['position_kind']=='cell':
                for field in ('words','lines'):
                    kept=[w for w in chunk.get(field,[]) if not contains(box,rect(w['bbox']))]
                    if any(overlaps(box,rect(w['bbox'])) for w in kept if w.get('table_index')==target['table_index']):
                        raise ValueError('格框截断文字，须通过完整表格核对修订')
                    if effective:kept.append(dict(text=effective,bbox=box,source='manual_revision',position_kind='review_region_not_glyph',table_index=target['table_index'],manual_confirmation=deepcopy(record)))
                    chunk[field]=kept
            rows=deepcopy(table.get('rows') or table.get('raw_rows'))
            if not rows:
                rows=[['']*max(c['column']+c['colspan'] for c in table['cells']) for _ in range(max(c['row']+c['rowspan'] for c in table['cells']))]
            for c in table['cells']:rows[c['row']][c['column']]=c.get('text','')
            table.update(rows=rows,raw_rows=deepcopy(rows),columns=rows[0],data=rows[1:])
            table['markdown']='\n'.join('| '+' | '.join(str(v).replace('|','\\|').replace('\n',' ') for v in row)+' |' for row in rows)
        elif target['target_kind']=='line':
            for field in ('words','lines'):
                old=chunk.get(field,[])
                if any(overlaps(box,rect(w['bbox'])) and not contains(box,rect(w['bbox'])) for w in old):
                    raise ValueError('区域与相邻正文交叠，请使用已有完整区域修订')
                chunk[field]=[w for w in old if not contains(box,rect(w['bbox']))]
                if effective:chunk[field].append(dict(text=effective,bbox=box,source='manual_revision',position_kind='review_region_not_glyph',manual_confirmation=deepcopy(record)))
                chunk[field].sort(key=lambda w:(w['bbox'][1],w['bbox'][0]))
        else:
            chunk['text']=effective;chunk['raw_text']=effective
        if target['target_kind'] not in ('chunk','logical_cell'):
            tables=chunk.get('tables',[])
            outside=[l for l in (chunk.get('lines') or chunk.get('words',[])) if not any(contains(rect(t['bbox_pdf']),rect(l['bbox'])) for t in tables if t.get('bbox_pdf'))]
            parts=[(l['bbox'][1],l['bbox'][0],l['text']) for l in outside]+[(t['bbox_pdf'][1],t['bbox_pdf'][0],t.get('markdown','')) for t in tables]
            chunk['text']=chunk['raw_text']='\n'.join(v for _,__,v in sorted(parts))
        chunk.setdefault('manual_content_states',{})[item['issue_key']]=state
        chunk['manual_content_applied']=True
        saved.append(record)
    return output,resolved,saved
