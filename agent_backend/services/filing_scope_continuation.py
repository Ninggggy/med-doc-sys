"""范围续行只在同一来源单元中连接；不识别、不补字、不修正OCR。"""
import re


def _same_flow(left, right):
    for key in ('chunk', 'page', 'table', 'row', 'cell', 'block_id', 'column_id'):
        if left.get(key) != right.get(key):
            return False
    a, b = left.get('bbox'), right.get('bbox')
    if a and b:
        height = max(a[3]-a[1], b[3]-b[1], 1)
        # 同行另一栏目、回跳以及过大的空白均不能当作续行。
        return b[1] >= a[3]-height*.35 and b[1]-a[3] <= height*2.5 and abs(b[0]-a[0]) <= height*3
    return right.get('line') == left.get('line', 0)+1


def scope_lines(lines, field_pattern, labels):
    result = []
    active = None
    combined = False
    previous = None
    all_labels = tuple(label for values in labels.values() for label in values)
    for text, loc in lines:
        loc = dict(loc)
        bare_scope = loc.get('table') is None and text.strip() in ('生产范围', '生产地址和生产范围')
        if bare_scope:
            # 仅语法视图补分隔符；原始source_statements和标签原文不变。
            loc['label_without_delimiter'] = text
            text = text + '：'
        matches = list(field_pattern.finditer(text))
        last_label = ((matches[-1].group('label') or matches[-1].group('bracket')) if matches else '')
        # 未收录的字段标题也要截断，防止范围吸入登记机关、签发日期等。
        heading = bool(re.match(r'^[^：:（）()]{1,24}[：:]', text)) or text.strip('：: ') in all_labels
        if combined and (text.startswith('受托生产企业：') or re.match(r'^[^：:]+(?:号|区|镇|街道)[：:]', text)
                         or re.match(r'^[^：:]*[，,；;][^：:]*[：:]', text)):
            heading = False
        if active is not None and not matches and not heading and _same_flow(previous, loc):
            index = active
            old, origin = result[index]
            result[index] = (old+'\n'+text, {**origin, 'continuation_sources': origin.get('continuation_sources', [])+[loc]})
            previous = loc
            continue
        if active is not None and not matches and not heading:
            a,b=previous.get('bbox'),loc.get('bbox')
            same_region=all(previous.get(k)==loc.get(k) for k in ('chunk','page','table','block_id','column_id'))
            # 邻接正文之外的叠加小行不得改变正文续行锚点；保留独立原文和未决证据。
            if same_region and a and b and b[1] < a[3] and b[3] > a[1]:
                old,origin=result[active]
                result[active]=(old,{**origin,'internal_unresolved':origin.get('internal_unresolved',[])+[
                    {'reason':'overlapping_off_flow_line','text':text,'source':loc}]})
                result.append((text,loc))
                continue
        result.append((text, dict(loc)))
        active = len(result)-1 if last_label in ('生产范围','生产地址和生产范围') and loc.get('table') is None else None
        combined = last_label == '生产地址和生产范围'
        previous = loc
    return result


def scope_entries(raw, source):
    """仅按括号外明确分号拆项；限制续句随原项，不推断药名或地址。"""
    parts, start, depth = [], 0, 0
    for i, char in enumerate(raw):
        if char in '（(': depth += 1
        elif char in '）)': depth = max(0, depth-1)
        elif char in ';；' and depth == 0:
            parts.append(raw[start:i]); start = i+1
    parts.append(raw[start:])
    entries = []
    for part in parts:
        part = part.strip()
        if not part: continue
        if re.match(r'^(?:仅限|不得|不含)', part) and entries:
            entries[-1]['raw_value'] += '；'+part
            if len(entries) > 1:
                entries[-1]['unresolved'] = ['restriction_target_not_explicit']
        else:
            entries.append({'raw_value': part, 'source': dict(source), 'attribution': 'source_item_only'})
    for entry in entries:
        entry['restrictions'] = re.findall(r'(?:仅限|不得|不含)[^；;。]*', entry['raw_value'])
        entry['unresolved'] = entry.get('unresolved', []) or (['restriction_target_not_explicit'] if re.match(r'^(?:仅限|不得|不含)', entry['raw_value']) else [])
        if len(entries) > 1 and re.search(r'\n\s*(?:仅限|不得|不含)', entry['raw_value']):
            entry['unresolved'] = ['restriction_target_not_explicit']
        if entry['unresolved']:
            entry['attribution'] = 'requires_review'
    return entries
