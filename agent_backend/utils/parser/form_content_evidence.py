"""申请表的版面证据。原文和墨迹位置保留，填写区域与模板结构分开。"""
import re
from statistics import median

from agent.agent_backend.utils.parser.form_field_semantics import heading


def template_instruction(text):
    # 只识别独立的填表操作指令；业务字段中的「说明」或数字不走此规则。
    return bool(re.fullmatch(r'(?:填(?:写|表)说明|填表须知|填写要求)\s*[:：]\s*(?:\d+[.、]\s*)?(?:请|应|须|必须).+', text.strip(),re.S))


def filling_label(text):
    """已知业务标题或明确要求填写叙述的栏目；未知并列文字不是填写格证据。"""
    text = str(text).strip(' :：')
    if heading(text):
        return True
    if re.fullmatch(r'(?:填(?:写|表)说明|填表须知|填写要求)',text):
        return False
    return bool(re.fullmatch(r'.*(?:自填栏目|填写内容|补充内容|补充说明|备注|附注|意见|情况描述)',text))


def _contains(box, other):
    return box[0] <= (other[0]+other[2])/2 <= box[2] and box[1] <= (other[1]+other[3])/2 <= box[3]


def _rows(lines):
    rows = []
    for line in sorted(lines, key=lambda l: (l['bbox'][1], l['bbox'][0])):
        box = line['bbox']
        row = next((r for r in reversed(rows) if min(r[0]['bbox'][3], box[3]) > max(r[0]['bbox'][1], box[1])), None)
        if row is None:
            rows.append([line])
        else:
            row.append(line)
    return [sorted(row, key=lambda l: l['bbox'][0]) for row in rows]


def pdf_form_structure(pages):
    """依据字段同行、真实单元格、页边孤立/重复结构记录可解释的排除依据。"""
    regions = []
    for page in pages:
        lines = [l for l in page.get('lines', []) if l.get('bbox') and str(l.get('text', '')).strip()]
        if not lines:
            continue
        page_box = page.get('page_bbox')
        row_groups = _rows(lines)
        heights = [l['bbox'][3]-l['bbox'][1] for l in lines]
        line_height = median(heights)
        cells = [c['bbox_pdf'] for t in page.get('tables', []) for c in t.get('cells', []) if c.get('bbox_pdf')]
        headings = [l for l in lines if heading(l['text'])]
        filling_cells = []
        for table in page.get('tables', []):
            table_cells = [c['bbox_pdf'] for c in table.get('cells', []) if c.get('bbox_pdf')]
            labels = [cell for cell in table_cells if any(_contains(cell,h['bbox']) for h in headings)]
            # 使用标签单元格的跨度；其墨迹可能只在跨行合并格的顶部。
            filling_cells.extend(cell for cell in table_cells if any(
                cell == label or (label[2] <= cell[0] and min(label[3],cell[3]) > max(label[1],cell[1]))
                for label in labels))
        detail_column = None
        for row in row_groups:
            texts = [l['text'].strip() for l in row]
            row_instruction = not any(heading(t) for t in texts) and template_instruction(':'.join(texts))
            if texts and texts[0] == '序号' and len(texts) > 1:
                detail_column = (row[0]['bbox'][0], row[1]['bbox'][0])
            for line in row:
                text, box = line['text'].strip(), line['bbox']
                field_row = any(heading(l['text']) for l in row)
                own_heading = heading(text)
                field_cell = any(_contains(cell, box) and any(_contains(cell, h['bbox']) for h in headings) for cell in cells)
                # 已有真实填写格中纵向排版的内容，不能因含「填写说明」被删除。
                value_cell = any(_contains(cell,box) for cell in filling_cells)
                anchored = bool(own_heading or field_row or field_cell or value_cell)
                role, reason = 'body', '正文；尚须结合字段或填写单元格确认'
                if not anchored and (template_instruction(text) or row_instruction):
                    role, reason = 'template', '字段外的填表操作说明'
                elif (not anchored and detail_column and len(row) == 1 and re.fullmatch(r'\d+[.、]?', text)
                      and detail_column[0] <= box[0] < detail_column[1]):
                    role, reason = 'template', '序号列中只有编号，明细填写列为空'
                elif not anchored and page_box:
                    height = page_box[3]-page_box[1]
                    marginal = box[3] <= page_box[1]+height*.08 or box[1] >= page_box[3]-height*.08
                    # 页边本身并非充分条件：与正文分离或在其他页相同边缘位置重复。
                    others = [l for l in lines if l not in row and not template_instruction(l['text'])]
                    separation = min((max(l['bbox'][1]-box[3], box[1]-l['bbox'][3], 0) for l in others), default=0)
                    repeated = any(other.get('page') != page.get('page') and any(
                        str(l.get('text', '')).strip() == text and l.get('bbox') and
                        abs(l['bbox'][1]/max(other.get('page_bbox', [0,0,0,1])[3],1)-box[1]/max(page_box[3],1)) < .02
                        for l in other.get('lines', [])) for other in pages)
                    if marginal and (separation > line_height*2 or repeated):
                        role = 'template' if repeated or re.fullmatch(r'(?:\d+|第\s*\d+\s*页(?:\s*共\s*\d+\s*页)?)', text) else 'uncertain'
                        reason = '与正文分离的页边页码/重复页眉页脚' if role == 'template' else '孤立页边文字，无法确认是填写还是页眉页脚'
                regions.append({'page':page['page'], 'bbox_pdf':list(box), 'coordinate_unit':'pdf_point',
                                'text':text, 'role':role, 'reason':reason,
                                'field_anchor':anchored, 'row_values':texts})
    return {'regions':regions, 'body_text':'\n'.join(r['text'] for r in regions if r['role']=='body')}


def form_page_lines(page, structure):
    excluded = [r['bbox_pdf'] for r in structure['regions'] if r['page']==page['page'] and r['role'] != 'body']
    return {**page, **{key:[l for l in page.get(key, []) if not any(_contains(box,l['bbox']) for box in excluded)]
                      for key in ('lines','words')}}


def word_form_structure(doc):
    regions = []
    active_field = False
    for index, paragraph in enumerate(doc.paragraphs):
        text = paragraph.text.strip()
        if heading(text):
            active_field = True
        if text:
            instruction = template_instruction(text) and not active_field
            regions.append({'text':text, 'paragraph':index, 'coordinate_unit':'word_paragraph',
                            'role':'template' if instruction else 'body',
                            'reason':'字段外的填表操作说明' if instruction else '正文段落'})
    for index, section in enumerate(doc.sections):
        for part in ('header','footer'):
            for paragraph in getattr(section,part).paragraphs:
                if paragraph.text.strip():
                    regions.append({'text':paragraph.text.strip(),'section':index,'part':part,
                                    'coordinate_unit':'word_'+part,'role':'template','reason':'Word页眉页脚结构'})
    def walk(table, location):
        numbered = None
        column_labels = []
        for row_index, row in enumerate(table.rows):
            repeated_header = any(node.get('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val','1')
                                  not in ('0','false','off') for node in row._tr.xpath('./w:trPr/w:tblHeader'))
            cells = []
            for cell in row.cells:
                if not any(c._tc is cell._tc for c in cells):
                    cells.append(cell)
            texts = [c.text.strip() for c in cells]
            if repeated_header:
                column_labels = texts
            row_instruction = not any(heading(t) for t in texts) and template_instruction(':'.join(texts))
            if '序号' in texts:
                numbered = texts.index('序号')
            number_only = numbered is not None and all(not t or (i==numbered and re.fullmatch(r'\d+[.、]?',t)) for i,t in enumerate(texts))
            for ci, (cell, text) in enumerate(zip(cells,texts)):
                if text:
                    instruction = row_instruction or (len([t for t in texts if t]) == 1 and template_instruction(text) and not heading(text))
                    regions.append({'text':text,'table':location,'row':row_index,'column':ci,'row_values':texts,
                                    'column_label':column_labels[ci] if not repeated_header and ci<len(column_labels) else '',
                                    'coordinate_unit':'word_table','role':'template' if number_only or instruction or repeated_header else 'body',
                                    'reason':'Word重复列头结构' if repeated_header else '只有序号的空明细行' if number_only else '独立填表操作说明' if instruction else '表格单元格'})
                for ti,nested in enumerate(cell.tables):
                    walk(nested,f'{location}/r{row_index}/c{ci}/t{ti}')
    for index,table in enumerate(doc.tables):
        walk(table,str(index))
    return {'regions':regions,'body_text':'\n'.join(r['text'] for r in regions if r['role']=='body')}
