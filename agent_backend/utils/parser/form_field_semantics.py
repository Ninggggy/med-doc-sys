"""字段标题和互斥选项的共同规则，不执行文件读取或 OCR。"""
import re


CHECKED_MARKS = '☑☒√✓✔■●▣'
UNCHECKED_MARKS = '☐□○'
MARK_PATTERN = r'[' + CHECKED_MARKS + UNCHECKED_MARKS + r']|[（(\[]\s*[xXvV√✓✔]?\s*[）)\]]'


def _selection_segments(fragment):
    """先保留明确选项边界；名称括号内的标点不分段，括号勾选符号整体读取。"""
    source = str(fragment or '')
    pairs = {'(': ')', '（': '）', '[': ']', '【': '】'}
    stack, start, segments = [], 0, []
    for match in re.finditer(MARK_PATTERN + r'|[;；|()（）\[\]【】]', source):
        token = match.group()
        if re.fullmatch(MARK_PATTERN, token):
            continue
        if token in pairs:
            stack.append(pairs[token])
        elif stack and token == stack[-1]:
            stack.pop()
        elif token in ';；|' and not stack:
            segments.append(source[start:match.start()])
            start = match.end()
    segments.append(source[start:])
    for segment in segments:
        lines = [line for line in segment.splitlines() if line.strip()]
        # 每行都有符号和名称才是独立选项；名称续行仍属于同一段。
        if len(lines) > 1 and all(re.search(MARK_PATTERN, line) and re.sub(MARK_PATTERN, '', line).strip() for line in lines):
            yield from lines
        else:
            yield segment


def selection_evidence(text, options=None, *, multiple=False, require_mark=False):
    """保留每个候选的原文与三态选择；只在符号归属唯一时确认。

    list 的每项是独立单元格/区域，不能跨边界借用符号。
    options=None 用于原表自由命名的剂型、事项，内部编号另行映射。
    """
    fragments = list(text) if isinstance(text, (list, tuple)) else [text]
    fragments = [segment for fragment in fragments for segment in _selection_segments(fragment)]
    entries, ambiguous, marked = [], False, False
    for fragment in fragments:
        source = str(fragment or '').strip()
        if not source:
            continue
        # 已知选项优先最长名称，避免“非处方药”“不使用”被截断。
        if options is not None:
            pattern = '|'.join(re.escape(x) for x in sorted(options, key=len, reverse=True))
            if not pattern:
                continue
            normalized = re.sub(r'[ \t]+', '', source).replace('是否', '')
            tokens = [(m.group(), bool(re.fullmatch(MARK_PATTERN, m.group())))
                      for m in re.finditer(MARK_PATTERN + '|' + pattern, normalized)]
        else:
            tokens = []
            token_source = source
            wrapped = re.fullmatch(r'(\d+\.\d+[^\n]*?)(' + MARK_PATTERN + r')\s*\n(.+)', source, re.S)
            if wrapped and not re.search(MARK_PATTERN + r'|(?<![\d.])\d+\.\d+', wrapped[3]):
                # 同一已定位单元格中，事项名称续行在后置符号下一行；原文仍完整留存。
                token_source = wrapped[1] + '\n' + wrapped[3] + wrapped[2]
            for part in re.split('(' + MARK_PATTERN + ')', token_source):
                if not part or not part.strip():
                    continue
                if re.fullmatch(MARK_PATTERN, part):
                    tokens.append((part, True))
                else:
                    separators = r'(?<![\d.])(?=\d+\.\d+\s*[^\d.])'
                    if not re.search(MARK_PATTERN, source):
                        separators += r'|\n+'
                    for label in re.split(separators, part):
                        label = label.strip(' \t\r，,。')
                        if label:
                            tokens.append((label, False))
        local = {}
        for i, (label, is_mark) in enumerate(tokens):
            if is_mark:
                continue
            match = re.match(r'^(\d+\.\d+)\s*(.*)$', label, re.S)
            local[i] = {'code': match[1] if match else '', 'name': match[2] if match else label,
                        'source_text': source, 'selected': None, 'status': 'unconfirmed'}
            if options is None and re.fullmatch(r'\d+[.、．]\s*已上市.*药学变更', label):
                # 管理类别标题不是待勾选的叶子事项，也不妨碍其唯一明确填写值。
                local[i]['status'] = 'category'
        has_mark = any(is_mark for _, is_mark in tokens)
        marked |= has_mark
        if has_mark:
            assignments = []
            for direction in (-1, 1):
                states = {}
                for i, (mark, is_mark) in enumerate(tokens):
                    if not is_mark:
                        continue
                    j = i + direction
                    if j not in local or j in states:
                        break
                    states[j] = bool(any(c in CHECKED_MARKS + 'xXvV' for c in mark))
                else:
                    assignments.append(states)
            if assignments and all(a == assignments[0] for a in assignments):
                for i, selected in assignments[0].items():
                    local[i].update(selected=selected, status='selected' if selected else 'unselected')
            else:
                ambiguous = True
                for entry in local.values():
                    entry['status'] = 'ambiguous'
        entries.extend(local.values())
    # 没有勾选符号时，只接受唯一的明确填写值；候选清单不等于选择。
    if not marked and not require_mark:
        names = {e['name'] for e in entries if e['status'] != 'category'}
        if len(names) == 1:
            for entry in entries:
                if entry['status'] != 'category':
                    entry.update(selected=True, status='explicit')
    conflicts = set()
    for entry in entries:
        identity = (entry['code'], entry['name'])
        states = {e['selected'] for e in entries if (e['code'], e['name']) == identity and e['selected'] is not None}
        if len(states) > 1:
            conflicts.add(identity)
    for entry in entries:
        if (entry['code'], entry['name']) in conflicts:
            entry.update(selected=None, status='conflict')
    selected = list(dict.fromkeys(e['name'] for e in entries if e['selected'] is True))
    conflict = bool(conflicts) or (not multiple and len(selected) > 1)
    if not multiple and (ambiguous or conflict):
        selected = []
    return {'value': selected[0] if len(selected) == 1 else '', 'selected_values': selected,
            'status': 'conflict' if conflict else 'ambiguous' if ambiguous else 'confirmed' if selected else 'unconfirmed',
            'marked': marked, 'entries': entries}


def option_evidence(text, options):
    return selection_evidence(text, options)


def selected_option(text, options):
    return option_evidence(text, options)['value']


ALIASES = {
    1: ['本申请属于', '申请类型'], 2: ['药品注册分类'], 3: ['是否为OTC', 'OTC'],
    4: ['原申请品种状态'], 5: ['申请事项分类', '备案事项分类', '申请事项'],
    6: ['药品通用名称', '通用名称', 'Generic name'], 7: ['英文名称/拉丁名称', '药品英文名称', '药品拉丁名称', '英文名称', '拉丁名称'],
    8: ['汉语拼音', '拼音'], 9: ['化学名称'], 10: ['商品名称'], 11: ['剂型'],
    12: ['含量规格', '规格'], 13: ['同品种已被受理或同期申报的其他制剂规格', '同品种其他规格'],
    14: ['包装'], 15: ['药品有效期', '有效期'], 16: ['处方'], 17: ['原/辅料/包材来源', '原辅包来源', '原辅料包材来源'],
    18: ['中药材标准'], 19: ['受理前药品注册检验'], 20: ['主要适应症或者功能主治', '适应症/功能主治', '适应症', '功能主治'],
    21: ['补充申请的内容', '备案的内容', '变更内容'], 22: ['提出补充申请的理由', '提出补充申请理由', '备案理由', '变更理由'],
    23: ['原批准注册内容及相关信息', '原批准信息'], 24: ['专利情况'], 25: ['数据保护相关内容', '数据保护'],
    26: ['中药品种保护'], 27: ['同品种新药监测期', '同品种新药检测期'], 28: ['本次申请为', '本次申请涉及事项'],
    29: ['历次申请情况', '其他相关情况'], 30: ['药品注册申请人', '申请人信息', '上市许可持有人', '申请人'],
    31: ['制剂生产企业', '生产企业信息', '生产企业'], 32: ['委托研究机构信息', '委托研究机构'],
    33: ['其他特别申明事项'],
}


def heading(text):
    source = str(text or '').strip()
    source = re.sub(r'^\d{1,2}[.、．,，]\s*(?!\d)', '', source)
    for number, label in sorted(((n, s) for n, labels in ALIASES.items() for s in labels), key=lambda x: len(x[1]), reverse=True):
        pattern = r'\s*'.join(re.escape(c) for c in label)
        suffix = r'|[（(]\s*分类\s*[:：]' if number == 20 else ''
        match = re.match(pattern + r'(?=\s|[:：]|$' + suffix + ')', source)
        if match:
            remainder = source[match.end():].lstrip(' :：\t\r\n')
            # Word 标签的未闭合“（分类：”是填写提示，本身没有分类值。
            if number == 20 and re.fullmatch(r'[（(]\s*分类\s*[:：]\s*', remainder):
                remainder = ''
            return number, label, remainder
    return None


def normalized_validity(value):
    """仅比较明确年月；原文另存，不近似换算天数。"""
    from decimal import Decimal
    compact = re.sub(r'\s+', '', str(value))
    match = re.fullmatch(r'(?:(\d+(?:\.\d+)?)年(?:(\d+)个?月)?|(\d+)个?月)', compact)
    if not match:
        return compact
    months = Decimal(match[1] or '0') * 12 + Decimal(match[2] or match[3] or '0')
    # JSON证据使用整数或精确十进制字符串，避免二进制浮点误差。
    return int(months) if months == months.to_integral_value() else format(months.normalize(), 'f') + '个月'


def validity_evidence(text, context=''):
    source = str(text or '')
    positions = [i for i, char in enumerate(source) if not char.isspace()]
    compact = ''.join(source[i] for i in positions)
    month = r'(?<![\d.])((?:\d+(?:\.\d+)?年(?:\d+个?月)?|\d+个?月))(?![\d.年月日号半个])'
    subject = re.compile(r'(?P<other>(?:证书|许可证|注册证|资质|批件)(?:的)?有效期|稳定性(?:考察|研究)?(?:(?:的)?有效期)?|加速(?:试验)?|考察(?:时长|时间)?|试验(?:时长|时间)?|检验周期|复验(?:(?:的)?有效)?期|截止日期|截止)|(?P<drug>(?:(?:本品|药品|制剂|产品)(?:的)?)?(?:原批准|获批|拟延长后|拟变更后|变更后|原)?有效期)')
    segments = []
    phase = re.compile(r'(?P<historical>此前|以往|历史|上次|上一阶段|原批准时|当时)|(?P<current>本次|此次|现申请|现拟申请|本申请|目前|现阶段)')
    stage, marker = 'current', ''
    # 对象与阶段分别作用于片段；明确阶段转换立即生效，不依赖逗号/分号。
    for sentence_match in re.finditer(r'[^。；;]+', compact):
        sentence = sentence_match[0]
        events = [(0, 'object', 'drug' if context == '药品有效期' else 'unknown', '')]
        events += [(m.start(), 'object', 'drug' if m.lastgroup=='drug' else 'other', '') for m in subject.finditer(sentence)]
        events += [(m.start(), 'stage', m.lastgroup, m[0]) for m in phase.finditer(sentence)]
        events.sort(key=lambda event:event[0])
        obj = 'unknown'
        for i, (start, kind, value, word) in enumerate(events):
            if kind == 'object':
                obj = value
            else:
                stage = 'uncertain' if re.search(r'(?:或|可能|未明确|不确定)[，,]*$', sentence[:start]) else value
                marker = word
            end = events[i+1][0] if i+1 < len(events) else len(sentence)
            if end > start:
                segments.append((obj, sentence[start:end], stage, marker, sentence_match.start()+start))
    candidates, issues = [], []
    labels = [('original_validity_period', r'(?:原有效期|原批准(?:有效期)?|获批有效期)'),
              ('proposed_validity_period', r'(?:拟延长后有效期|拟变更后有效期|变更后有效期|拟变更为|拟延长至)')]
    # 先词法读取完整期限（含不支持的半月/小数月/范围），再作精确解释。
    # 普通叙述不是期限；也不能仅取一个合法前缀而丢弃单位修饰。
    duration = r'(?:约|近|至少|至多|不少于|不超过)?\d[\d.年月个天日周半又余多零一二两三四五六七八九十百]*(?:左右|上下)?'
    duration += r'(?:(?:至|到|~|～|—|-)\d[\d.年月个天日周半又余多]*)?'
    connector = r'(?:延长至|延长到|变更为|调整为|→|->)'
    for obj, segment, stage, marker, offset in segments:
        historical = stage == 'historical'
        meta = {'historical':historical, 'stage':stage, 'stage_marker':marker, 'object':obj,
                'segment_text':source[positions[offset]:positions[offset+len(segment)-1]+1]}
        relation = list(re.finditer('('+duration+')'+connector+'('+duration+')', segment))
        if obj != 'drug':
            if obj == 'unknown' and (relation or any(re.search(label+r'[:：为|]*'+month, segment) for _, label in labels)):
                issues.append({'reason':'月份变化对象不明确，待核对', 'source_text':segment})
            continue
        for match in relation:
            ends = {'original_validity_period':match[1], 'proposed_validity_period':match[2]}
            tail = segment[match.end():]
            end = offset+match.end()
            newline_boundary = (end < len(positions) and '\n' in source[positions[end-1]+1:positions[end]])
            if tail and not newline_boundary and not re.match(r'^[，,。；;|（(）)]|^(?:并|且|同时|后|用于|按|自|其他|其余|不涉及|以|继续|在|而|但|说明|附)', tail):
                # 未知紧接修饰不能冒充已支持期限，完整保留这一端供核对。
                ends['proposed_validity_period'] += re.split(r'[，,|；;。]', tail)[0]
            unsupported = [(key, value) for key, value in ends.items() if not re.fullmatch(month, value)]
            for key, value in unsupported:
                issues.append({'reason':'药品期限关系'+('原期限' if key.startswith('original') else '拟变更期限')+'无法准确解释，保留完整原文待核对',
                               'path':key, 'expression':value, 'source_text':segment, 'relation_text':match[0], **meta})
            if not unsupported:
                candidates.append({**ends, **meta, 'relation_text':match[0], 'relation_values':dict(ends)})
        labeled_ends = [m for _, label in labels for m in re.finditer(label+r'[:：为|]*'+month, segment)]
        # “拟变更为24个月”是明确的单端标签，并非缺少左端的关系。
        # 只排除被完整标签值覆盖的连接词，其他无法解析的关系仍须诊断。
        unexplained = [m for m in re.finditer(connector, segment)
                       if not any(e.start() <= m.start() and e.end() > m.end() for e in labeled_ends)]
        if obj=='drug' and unexplained and not relation:
            issues.append({'reason':'药品期限关系无法完整解释，保留原文待核对', 'source_text':segment, **meta})
        if obj=='drug' and stage=='uncertain' and (relation or '不变' in segment):
            issues.append({'reason':'药品期限所属历史或本次申请阶段不明确，待核对', 'source_text':segment, **meta})
        if re.fullmatch(r'(?:本品|药品|制剂|产品)?(?:的)?有效期(?:保持)?不变[，,]*', segment):
            candidates.append({'unchanged_declaration':True, 'source_text':segment, **meta})
        for key, label in labels:
            for match in re.finditer(label+r'[:：为|]*'+month, segment):
                candidates.append({key:match[1], **meta})
        if not relation and not any(re.search(label, segment) for _,label in labels):
            match = re.fullmatch(r'(?:本品|药品|制剂|产品)?(?:的)?(?:有效期)?[:：|]*(?:保持(?:不变)?|仍为|为)?[（(]?'+month+r'[）)]?(?:不变|保持不变)?[，,]*', segment)
            if match:
                candidates.append({'reported_value': match[1], **meta})
                if re.search(r'保持|不变|仍为', segment):
                    candidates.append({'unchanged_value':match[1], **meta})
        if not relation and re.search(r'\d.*(?:年|月)', segment) and not re.search(month, segment):
            issues.append({'reason':'药品期限表达无法准确解释，保留完整原文待核对', 'source_text':segment})
    merged = {}
    for key in ('original_validity_period','proposed_validity_period','reported_value'):
        raw_values = [c[key] for c in candidates if key in c and c['stage']=='current']
        values = {normalized_validity(v) for v in raw_values}
        if len(values) == 1: merged[key] = raw_values[0]
        elif len(values)>1:
            issues.append({'reason':'药品有效期存在多个冲突值，待核对', 'source_text':source, 'values':sorted(values, key=str)})
    unchanged = [c['unchanged_value'] for c in candidates if 'unchanged_value' in c and c['stage']=='current']
    relations = [c[k] for c in candidates for k in ('original_validity_period', 'proposed_validity_period') if k in c and c['stage']=='current']
    if any(normalized_validity(u) != normalized_validity(v) for u in unchanged for v in relations):
        issues.append({'reason':'药品有效期保持不变与变更关系冲突，待核对', 'source_text':source})
    declarations = [c for c in candidates if c.get('unchanged_declaration') and c['stage']=='current']
    changed = [c for c in candidates if c['stage']=='current' and 'original_validity_period' in c and 'proposed_validity_period' in c
               and normalized_validity(c['original_validity_period']) != normalized_validity(c['proposed_validity_period'])]
    if declarations and changed:
        issues.append({'reason':'药品有效期保持不变与变更关系冲突，待核对', 'source_text':source,
                       'candidates':declarations+changed})
    if any('冲突' in issue['reason'] for issue in issues):
        merged.pop('original_validity_period', None)
        merged.pop('proposed_validity_period', None)
    return {'values':{k:v for k,v in merged.items() if k != 'reported_value'},
            'reported_value':merged.get('reported_value',''), 'issues':issues, 'candidates':candidates}


def validity_values(text, context=''):
    return validity_evidence(text, context)['values']
