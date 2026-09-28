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
        for ti,table in enumerate(chunk.get('tables',[])):
            for cidx,cell in enumerate(table.get('cells',[])):
                roles={e['span_id'] for e in cell.get('role_evidence',[]) if e.get('role')=='red_overlay'}
                for span in cell.get('result',{}).get('spans',[]):
                    if span.get('span_id') not in roles or not span.get('polygon_page'):continue
                    poly=span['polygon_page'];box=[min(v[0] for v in poly),min(v[1] for v in poly),max(v[0] for v in poly),max(v[1] for v in poly)]
                    result.append({**base,'issue_key':f'target:{doc_id}:{ci}:overlay:{ti}:{cidx}:{span["span_id"]}',
                        'target_kind':'overlay','table_index':ti,'cell_index':cidx,'source_span_ids':[span['span_id']],
                        'bbox_pdf':box,'position_kind':'overlay','value':span['text_raw'],'message':'印章/叠印片段：'+span['text_raw']})
        lines = chunk.get('lines') or chunk.get('words') or []
        used=set()
        for ei,element in enumerate(chunk.get('readable_elements',[])):
            ids=element.get('source_span_ids') or []
            if element.get('kind') not in ('field','paragraph') or not ids:continue
            used.update(ids)
            result.append({**base,'issue_key':f'target:{doc_id}:{ci}:element:{ei}','target_kind':'element',
                'element_index':ei,'region_id':element['region_id'],'source_span_ids':ids,
                'bbox_pdf':element['bbox_pdf'],'position_kind':'field_or_paragraph','value':element['text'],
                'message':(element.get('label') or '段落')+'：'+element['text'][:45]})
        for li,line in enumerate(lines):
            box=line.get('bbox')
            if line.get('source_span_id') in used or not box or any(contains(rect(t['bbox_pdf']),rect(box)) for t in chunk.get('tables',[]) if t.get('bbox_pdf')):continue
            result.append({**base,'issue_key':f'target:{doc_id}:{ci}:line:{li}','target_kind':'line',
                'line_index':li,'source_span_ids':[line.get('source_span_id')],'bbox_pdf':list(box),'position_kind':'line_or_region',
                'value':str(line.get('text','')),'message':'正文区域：'+str(line.get('text',''))[:45]})
        # 已有布局区域没有文字对象时，复用区域文字修订目标；不挂到整页。
        for region in chunk.get('layout_regions',[]):
            rid=region['region_id'];box=region['bbox_pdf']
            if any(t.get('region_id')==rid for t in result if t['chunk_index']==ci):continue
            related=[e for e in chunk.get('errors',[]) if e.get('region_id')==rid and e.get('code')=='ocr_coverage']
            if not related or any(overlaps(rect(box),rect(l['bbox'])) for l in lines if l.get('bbox')):continue
            result.append({**base,'issue_key':f'target:{doc_id}:{ci}:region:{rid}','target_kind':'line',
                'region_id':rid,'source_span_ids':[],'bbox_pdf':box,'position_kind':'line_or_region',
                'value':'','message':'未读出文字的版面区域'})
        if not lines and not chunk.get('tables') and str(chunk.get('text') or chunk.get('raw_text') or '').strip():
            result.append({**base, 'issue_key': f'target:{doc_id}:{ci}:chunk', 'target_kind': 'chunk',
                'bbox_pdf': chunk.get('page_bbox') or [], 'position_kind': 'document_chunk',
                'value': str(chunk.get('text') or chunk.get('raw_text')), 'message': f'原件内容块{ci+1}（无逐字坐标）'})
    return result


def review_issues(source, kind, chunks):
    from agent.agent_backend.services.filing_parse_readiness import source_issues
    diagnostics=source_issues(source,kind,chunks)
    targets=review_targets(source,kind,chunks)
    for target in targets:
        target['related_diagnostics']=[deepcopy(i) for i in diagnostics if i.get('edit_target_key')==target['issue_key']]
    return diagnostics+targets


def manual_pending(chunks):
    return [deepcopy(x) for chunk in chunks for x in chunk.get('manual_unreadable', [])]


def target_value(chunks, target):
    chunk=chunks[target['chunk_index']]
    if target['target_kind']=='overlay':
        return str(chunk.get('effective_overlays',{}).get(target['issue_key'],{}).get('text',target['value']))
    if target['target_kind']=='element':
        for e in chunk.get('readable_elements',[]):
            if e.get('region_id')==target.get('region_id') or any(l.get('manual_target_key')==target['issue_key'] for l in e.get('source_lines',[])):return e['text']
        return ''
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
        if target['target_kind']=='overlay':
            chunk.setdefault('effective_overlays',{})[target['issue_key']]=dict(text=effective,bbox_pdf=box,content_state=state,manual_confirmation=deepcopy(record))
        elif target['target_kind']=='element':
            ids=set(target['source_span_ids'])
            for collection in ('words','lines'):
                old=chunk.get(collection,[]);revised=[];inserted=False
                for word in old:
                    if word.get('source_span_id') in ids:
                        if not inserted:
                            revised.append(dict(text=effective,bbox=box,source='manual_revision',
                                source_span_id=word.get('source_span_id'),source_span_ids=list(target['source_span_ids']),
                                original_source_lines=deepcopy([w for w in old if w.get('source_span_id') in ids]),
                                manual_target_key=target['issue_key'],position_kind='review_region_not_glyph',
                                manual_confirmation=deepcopy(record)))
                            inserted=True
                    else:revised.append(word)
                chunk[collection]=revised
        elif target['target_kind']=='logical_cell':
            table=chunk['tables'][target['table_index']]
            rows=deepcopy(table.get('raw_rows') or table.get('rows'))
            rows[target['row']][target['column']]=effective
            from agent.agent_backend.services.filing_change_submission_parser import _FilingChangeDocxParser
            parser=_FilingChangeDocxParser('effective_document')
            structured=parser._build_structured_table(table.get('table_type',''),rows,table.get('caption',''))
            table.update(raw_rows=rows,rows=structured.get('rows',[]),headers=structured.get('headers',[]),
                         structured_data=structured,markdown=parser._table_to_markdown(rows))
            table.setdefault('manual_cell_states',{})[f"{target['row']}:{target['column']}"]=record
            # 有位置的正文和表格仍按统一区域投影，不能只留下表格。
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
                replacement=dict(text=effective,bbox=box,source='manual_revision',position_kind='review_region_not_glyph',manual_confirmation=deepcopy(record))
                revised=[]; inserted=False
                for word in old:
                    if contains(box,rect(word['bbox'])):
                        if not inserted and effective: revised.append({**word, **replacement})
                        inserted=True
                    else: revised.append(word)
                if not inserted and effective: revised.append(replacement)
                chunk[field]=revised
        else:
            chunk['text']=effective;chunk['raw_text']=effective
        if target['target_kind'] != 'chunk':
            from agent.ocr_service.paddle_runtime.assembly import assemble_chunk
            assemble_chunk(chunk, effective_revision=True, preserve_objects=target['target_kind'] in ('element','cell','logical_cell','overlay'))
        chunk.setdefault('manual_content_states',{})[item['issue_key']]=state
        chunk['manual_content_applied']=True
        related=item.get('related_issues',[])
        if not isinstance(related,list):raise ValueError('关联问题格式无效')
        reviewed=set()
        for entry in related:
            key=entry.get('issue_key') if isinstance(entry,dict) else None
            issue=next((i for i in issues if i['issue_key']==key),None)
            if (not issue or issue.get('edit_target_key')!=target['issue_key'] or key in reviewed
                    or not isinstance(entry.get('reason'),str) or not entry['reason'].strip()):
                raise ValueError('只能逐项处理明确归属本对象的问题，并填写核对依据')
            if state=='unreadable':raise ValueError('仍无法辨认时不能解除原问题')
            reviewed.add(key);resolved.add(key)
        record['related_issues']=deepcopy(related)
        saved.append(record)
    return output,resolved,saved


def attach_edit_targets(issues,source,kind,chunks):
    """字符来源优先；只有唯一完整对象才能接通动作，不能仅凭区域相交解除诊断。"""
    targets=review_targets(source,kind,chunks)
    supported={'ocr_quality','ocr_empty','ocr_score_unavailable','ocr_candidate_conflict','ocr_overlay_conflict','ocr_coverage','text_assembly_unresolved'}
    for issue in issues:
        if issue.get('code') not in supported:continue
        candidates=[t for t in targets if t['chunk_index']==issue.get('chunk_index')]
        sid=issue.get('source_span_id')
        exact=[t for t in candidates if sid and sid in t.get('source_span_ids',[])]
        if not exact and issue.get('original_lines'):
            ids={l.get('source_span_id') for l in issue['original_lines'] if l.get('source_span_id')}
            exact=[t for t in candidates if ids and ids.issubset(set(t.get('source_span_ids',[])))]
        if not exact and type(issue.get('cell_index')) is int:
            exact=[t for t in candidates if t['target_kind']=='cell' and t.get('table_index')==issue.get('table_index') and t.get('cell_index')==issue['cell_index']]
        if not exact and issue.get('region_id'):
            exact=[t for t in candidates if t.get('region_id')==issue['region_id']]
        if not exact and issue.get('bbox_pdf'):
            b=rect(issue['bbox_pdf'])
            exact=[t for t in candidates if t.get('bbox_pdf') and contains(rect(t['bbox_pdf']),b)]
        if len(exact)==1:
            issue['edit_target_key']=exact[0]['issue_key'];issue['edit_target_kind']=exact[0]['target_kind']
    return issues
