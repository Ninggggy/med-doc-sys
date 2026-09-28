"""有限候选CTC前向评分；不同权重不比较裸分数，原始张量不落盘。"""
import math
import unicodedata
from difflib import SequenceMatcher
import numpy as np

# 独立开发图像上的有限网格选择；仅组合同一候选集合内的相对序列支持。
# 不是跨模型裸分数比较，也不将此似然差解释成正确率。
SEQUENCE_PRIMARY_WEIGHT = .75
SEQUENCE_POOLED_MARGIN = 3.0


def punctuation_width_only(left,right):
    """仅同一种标点的 Unicode 宽度表示；不折叠单位、汉字、横线或缺失标点。"""
    if left==right:return True
    if len(left)!=len(right):return False
    def base(ch):
        parts=unicodedata.decomposition(ch).split()
        return chr(int(parts[1],16)) if len(parts)==2 and parts[0] in ('<wide>','<narrow>') and unicodedata.category(ch).startswith('P') else ch
    return all(a==b or (unicodedata.category(a).startswith('P') and unicodedata.category(b).startswith('P') and base(a)==base(b)) for a,b in zip(left,right))


def ctc_log_probability(probabilities, labels):
    # 含blank和重复字符的完整前向和；支持插入、删除、替换。
    labels=list(labels); states=[0]
    for label in labels: states.extend([label,0])
    if any(label<=0 or label>=probabilities.shape[1] for label in labels):return -math.inf
    logp=np.log(np.maximum(probabilities,1e-30));alpha=np.full(len(states),-np.inf)
    alpha[0]=logp[0,0]
    if labels:alpha[1]=logp[0,labels[0]]
    for frame in logp[1:]:
        prev=alpha;alpha=prev.copy()
        alpha[1:]=np.logaddexp(alpha[1:],prev[:-1])
        for j in range(2,len(states)):
            if states[j] and states[j]!=states[j-2]:alpha[j]=np.logaddexp(alpha[j],prev[j-2])
        alpha+=frame[states]
    return float(np.logaddexp(alpha[-1],alpha[-2])) if labels else float(alpha[0])


def candidate_alignment(probabilities,labels,characters):
    """候选自己的CTC最大路径，仅保存发射位置与后验，不保存帧张量。"""
    states=[0]
    for label in labels:states.extend([label,0])
    logp=np.log(np.maximum(probabilities,1e-30));previous=np.full(len(states),-np.inf)
    previous[0]=logp[0,0]
    if labels:previous[1]=logp[0,labels[0]]
    back=np.zeros((len(logp),len(states)),dtype=np.int32)
    for t in range(1,len(logp)):
        current=np.full(len(states),-np.inf)
        for j,label in enumerate(states):
            options=[j]
            if j:options.append(j-1)
            if j>1 and label and label!=states[j-2]:options.append(j-2)
            best=max(options,key=lambda k:previous[k]);back[t,j]=best;current[j]=previous[best]+logp[t,label]
        previous=current
    j=max([len(states)-1,max(0,len(states)-2)],key=lambda k:previous[k]);path=[]
    for t in range(len(logp)-1,-1,-1):path.append(j);j=back[t,j]
    path=path[::-1];tokens=[]
    for i,label in enumerate(labels):
        frames=[t for t,state in enumerate(path) if state==2*i+1]
        if not frames:return []
        t=max(frames,key=lambda t:probabilities[t,label])
        tokens.append(dict(text=characters[label],timestep=t,end_timestep=max(frames)+1,posterior=float(probabilities[t,label])))
    return tokens


def bounded_nbest(probabilities,characters,width=8):
    """有界CTC前缀束；只使用真实帧分布，不用词典扩写候选。"""
    beam={(): (0.,-math.inf)}
    for frame in probabilities:
        labels=set(np.argpartition(frame,-min(4,len(frame)))[-min(4,len(frame)):].tolist());labels.add(0)
        next_beam={}
        def add(prefix,blank=None,text=None):
            b,t=next_beam.get(prefix,(-math.inf,-math.inf))
            next_beam[prefix]=(float(np.logaddexp(b,blank)) if blank is not None else b,
                               float(np.logaddexp(t,text)) if text is not None else t)
        for prefix,(pb,pt) in beam.items():
            total=float(np.logaddexp(pb,pt))
            for label in labels:
                value=math.log(max(float(frame[label]),1e-30))
                if label==0:add(prefix,blank=total+value)
                elif prefix and prefix[-1]==label:
                    add(prefix,text=pt+value);add(prefix+(label,),text=pb+value)
                else:add(prefix+(label,),text=total+value)
        beam=dict(sorted(next_beam.items(),key=lambda x:np.logaddexp(*x[1]),reverse=True)[:width])
    return [''.join(characters[i] for i in prefix) for prefix in beam]


def edit_distance(left,right):
    row=list(range(len(right)+1))
    for i,a in enumerate(left,1):
        previous=row;row=[i]
        for j,b in enumerate(right,1):row.append(min(row[-1]+1,previous[j]+1,previous[j-1]+(a!=b)))
    return row[-1]


def local_competitions(primary,candidates,decoded,alternatives=()):
    # 当前有效主辅差异也必须进入竞争；历史候选不能定义对象的全部风险范围。
    intervals=[]
    for other in dict.fromkeys([*(candidates or []),decoded]):
        for op,a,b,c,d in SequenceMatcher(None,primary,other,autojunk=False).get_opcodes():
            if op!='equal':
                if b-a==d-c:
                    intervals.extend((a+i,a+i+1) for i in range(b-a) if primary[a+i]!=other[c+i])
                else:intervals.append((a,b))
    intervals=sorted(set(intervals));merged=[]
    for a,b in intervals:
        if merged and (a<merged[-1][1] or a==merged[-1][0] or a==b==merged[-1][1]):merged[-1]=(merged[-1][0],max(b,merged[-1][1]))
        else:merged.append((a,b))
    def boundary_map(other,a,b):
        opcodes=SequenceMatcher(None,primary,other,autojunk=False).get_opcodes();left=[];right=[]
        for tag,x,y,u,v in opcodes:
            if tag=='equal' or (tag=='replace' and y-x==v-u):
                if x<=a<=y:left.append(u+a-x)
                if x<=b<=y:right.append(u+b-x)
            if x==a:left.append(u)
            if y==b:right.append(v)
        if not left or not right:return None
        return min(left),max(right)
    result=[]
    for a,b in merged:
        mapped=boundary_map(decoded,a,b)
        if mapped is None:result.append(dict(start=a,end=b,available=False,reason='context_alignment_missing'));continue
        u,v=mapped;variants=[primary[a:b],decoded[u:v]]
        for candidate in [*(candidates or [decoded]),*alternatives]:
            span=boundary_map(candidate,a,b)
            if span is not None:variants.append(candidate[span[0]:span[1]])
        variants=list(dict.fromkeys(variants))
        candidate_dispute=any(span is not None and candidate[span[0]:span[1]]!=primary[a:b]
                              for candidate in (candidates or []) for span in [boundary_map(candidate,a,b)])
        result.append(dict(start=a,end=b,aux_start=a,aux_end=a+v-u,available=True,context='current_primary',
                           candidate_dispute=candidate_dispute,
                           variants=[dict(fragment=f,text=primary[:a]+f+primary[b:]) for f in variants],decoded_fragment=decoded[u:v]))
    return result


def score_candidates(probabilities, characters, candidates, decoded):
    alphabet={ch:i for i,ch in enumerate(characters)};rows=[]
    nbest=bounded_nbest(probabilities,characters)
    local=local_competitions(candidates[0],candidates[1:],decoded,nbest) if candidates else []
    expanded=[x['text'] for group in local if group.get('available') for x in group['variants']]
    for text in dict.fromkeys([*candidates,decoded,*expanded]):
        if any(ch not in alphabet or alphabet[ch]==0 for ch in text):
            rows.append(dict(text=text,available=False,reason='empty_or_out_of_alphabet'));continue
        value=ctc_log_probability(probabilities,[alphabet[ch] for ch in text])
        rows.append(dict(text=text,available=math.isfinite(value),log_probability=value,
                         alignment=candidate_alignment(probabilities,[alphabet[ch] for ch in text],characters)))
    return dict(method='full_ctc_forward_sum',primary_text=candidates[0] if candidates else None,candidate_texts=list(candidates),scores=rows,frames=int(probabilities.shape[0]),
                posterior_is_not_correctness=True,finite_candidate_set=True,local_competitions=local,nbest=nbest)


def decide_sequence(primary, candidates, response, geometry):
    from glyph_adjudication import input_evidence
    evidence=input_evidence(response['image'],metadata=geometry)
    result=dict(accepted=False,recovered=False,text=primary,glyphs=[],input_evidence=evidence,
                reason='missing_full_sequence_evidence',capability='implemented')
    if response.get('status')!='completed':result['reason']='request_not_completed';return result
    if not evidence['eligible'] and evidence['reason'] not in ('target_ink_touches_input_boundary','overlay_crosses_target_without_role_separation'):
        result['reason']='input_'+evidence['reason'];return result
    scoring=response.get('sequence_evidence',{})
    if scoring.get('method')!='full_ctc_forward_sum':return result
    if scoring.get('primary_text',primary)!=primary:
        result['reason']='effective_source_differs_from_scored_target';return result
    scores={x['text']:x['log_probability'] for x in scoring['scores'] if x.get('available') and type(x.get('log_probability')) in (int,float) and math.isfinite(x['log_probability'])}
    primary_review=response.get('primary_sequence_review',{})
    primary_scoring=primary_review.get('sequence_evidence',{})
    dual=(primary_review.get('status')=='completed' and primary_review.get('model')=='PP-OCRv6_medium_rec'
          and primary_review.get('image')==response['image'] and primary_review.get('source_geometry')==geometry
          and primary_scoring.get('method')=='full_ctc_forward_sum')
    primary_scores={x['text']:x['log_probability'] for x in primary_scoring.get('scores',[])
                    if x.get('available') and type(x.get('log_probability')) in (int,float) and math.isfinite(x['log_probability'])}
    if any(text not in scores for text in [primary,*candidates] if text):
        result['reason']='candidate_not_scored';return result
    aux=response.get('text','');tokens=response.get('ctc',{}).get('tokens',[])
    if not aux:result['reason']='auxiliary_unreadable';return result
    if any(type(t.get('posterior')) not in (int,float) or not math.isfinite(t['posterior']) or not 0<=t['posterior']<=1 for t in tokens):
        result['reason']='invalid_ctc_posterior';return result
    if ''.join(t['text'] for t in tokens)!=aux:result['reason']='token_text_mismatch';return result
    groups=scoring.get('local_competitions')
    if groups is None:result['reason']='scored_competitions_missing';return result
    edits=[];proof=[];unresolved=[];display_differences=[]
    expected_groups=local_competitions(primary,candidates,aux,scoring.get('nbest',[]))
    actual_ranges={(g['start'],g['end']) for g in groups}
    unresolved.extend(dict(g,reason='current_auxiliary_difference_not_scored') for g in expected_groups
                      if (g['start'],g['end']) not in actual_ranges)
    if not groups:
        if not evidence['eligible']:result['reason']='input_'+evidence['reason'];return result
        # 原值复核保留：完整序列必须一致，检查对象自身而非其他低分对象。
        if aux!=primary or not tokens or (not dual and min(t['posterior'] for t in tokens)<.97):
            result['reason']='original_sequence_unverified';return result
    for group in groups:
        if not group.get('available'):unresolved.append(group);continue
        variants=group['variants'];decoded=group['decoded_fragment'];a,b=group['start'],group['end'];u,v=group['aux_start'],group['aux_end']
        if all(punctuation_width_only(primary[a:b],variant['fragment']) for variant in variants):
            display_differences.append(dict(**group,reason='punctuation_width_representation_only',effective_fragment=primary[a:b]));continue
        if any(x['text'] not in scores for x in variants):unresolved.append(dict(**group,reason='candidate_not_scored'));continue
        if dual and any(x['text'] not in primary_scores for x in variants):
            unresolved.append(dict(**group,reason='primary_candidate_not_scored'));continue
        if dual:
            # 每个权重先相对同组最佳值中心化，避免整行长度或其他位置的分数影响比较。
            aux_max=max(scores[x['text']] for x in variants);main_max=max(primary_scores[x['text']] for x in variants)
            local_scores={x['text']:(1-SEQUENCE_PRIMARY_WEIGHT)*(scores[x['text']]-aux_max)+
                          SEQUENCE_PRIMARY_WEIGHT*(primary_scores[x['text']]-main_max) for x in variants}
        else:local_scores=scores
        chosen=max(variants,key=lambda x:local_scores[x['text']])
        decoded=chosen['fragment']
        selected_row=next(x for x in scoring['scores'] if x['text']==chosen['text'])
        selected_tokens=tokens if chosen['text']==aux else selected_row.get('alignment',[])
        if not selected_tokens:unresolved.append(dict(**group,reason='selected_candidate_alignment_missing'));continue
        v=u+len(decoded)
        # 同一个辅助权重、同一完整图像、相同上下文的有限序列似然比。
        competitors=[x for x in variants if x['fragment']!=decoded]
        margins=[(local_scores[chosen['text']]-local_scores[x['text']])/max(edit_distance(decoded,x['fragment']),1) for x in competitors]
        positions=list(range(u,v)) or list(range(max(0,u-1),min(len(selected_tokens),u+1)))
        minimum=min([selected_tokens[i]['posterior'] for i in positions] or [0])
        main_tokens=[]
        if dual:
            main_tokens=next(x.get('alignment',[]) for x in primary_scoring['scores'] if x['text']==chosen['text'])
            if len(main_tokens)!=len(chosen['text']) or len(selected_tokens)!=len(chosen['text']):
                unresolved.append(dict(**group,reason='selected_candidate_alignment_missing'));continue
            minimum=min([max(selected_tokens[i]['posterior'],main_tokens[i]['posterior']) for i in positions] or [0])
        classes=lambda fragment:{unicodedata.category(ch)[0] for ch in fragment}
        cross_category=any(classes(x['fragment'])!=classes(decoded) for x in competitors if x['fragment'] and decoded)
        required=.8 if dual else .995 if cross_category else .8
        record=dict(**group,selected_fragment=decoded,selected_sequence=chosen['text'],selected_alignment=selected_tokens,
                    margins=margins,min_disputed_posterior=minimum,required_posterior=required,cross_unicode_category=cross_category)
        if not group.get('candidate_dispute',True) and not dual:
            unresolved.append(dict(**record,reason='new_auxiliary_difference_requires_independent_sequence_review'));continue
        if dual:record['independent_sequence_support']=dict(primary_weight=SEQUENCE_PRIMARY_WEIGHT,
                    centered_scores=local_scores,primary_scores={x['text']:primary_scores[x['text']]-main_max for x in variants},
                    auxiliary_scores={x['text']:scores[x['text']]-aux_max for x in variants},
                    primary_alignment=[dict(index=i,**main_tokens[i]) for i in positions],
                    complete_alignment_source='bounded_review_responses.primary_sequence_review.sequence_evidence')
        if not evidence['eligible']:
            # 只缩小证据检查范围，不裁图、不伪造字符框。位置来自实际CTC发射和完整连通域。
            width,height=response['input_size'];step=max(width,height*320/48)/response['ctc']['timesteps']
            lo=max(0,u-1);hi=min(len(selected_tokens)-1,max(v,u+1))
            left=0 if lo==0 else (selected_tokens[lo-1]['timestep']+selected_tokens[lo]['timestep']+1)*step/2
            right=width if hi+1==len(selected_tokens) else (selected_tokens[hi]['timestep']+selected_tokens[hi+1]['timestep']+1)*step/2
            local=input_evidence(response['image'],metadata={**geometry,'focus_interval':[left,right]})
            record['local_input_evidence']=local
            if not local['eligible']:
                unresolved.append(dict(**record,reason='disputed_input_'+local['reason']));continue
        required_margin=SEQUENCE_POOLED_MARGIN if dual else 4.6
        if (margins and min(margins)<required_margin) or minimum<required:
            shape=None
            if v-u==1 and min([selected_tokens[i]['posterior'] for i in positions] or [0])>=.8 and margins and min(margins)>0:
                from glyph_adjudication import glyph_evidence
                expected='short_horizontal_stroke' if decoded=='-' else 'semicolon' if decoded in ';；' else 'colon' if decoded in ':：' else None
                selected_response={**response,'ctc':{**response['ctc'],'tokens':selected_tokens}}
                glyph=glyph_evidence(selected_response,u) if expected else None
                if expected and glyph and glyph['shape']==expected:shape=glyph
            if not shape:
                unresolved.append(dict(**record,reason='finite_local_competition_uncertain'));continue
            record['independent_shape_support']=shape
        proof.append(record);edits.append((a,b,decoded))
    # 零宽插入也有唯一位置；重复或相互覆盖的编辑不能重复应用。
    edits=sorted(set(edits));previous=None
    for edit in edits:
        a,b,_=edit
        if previous and (a<previous[1] or (a==b==previous[0]==previous[1])):
            result['reason']='overlapping_verified_edits';return result
        previous=edit
    text=primary
    for a,b,fragment in sorted(edits,reverse=True):text=text[:a]+fragment+text[b:]
    result['sequence_evidence']=dict(verified=proof,unresolved=unresolved,display_differences=display_differences,scoring=scoring,
                                   rules=dict(min_log_ratio_per_edit=SEQUENCE_POOLED_MARGIN if dual else 4.6,
                                              primary_weight=SEQUENCE_PRIMARY_WEIGHT if dual else None,
                                              min_disputed_posterior=.8,cross_category_posterior=None if dual else .995))
    # 完整目标几何与原始像素必需；仅有字符串/高分不能启用通用替换。
    if not geometry.get('target_polygon_input_px') or not geometry.get('source_input'):
        result['reason']='target_geometry_missing';return result
    preserved=(geometry.get('preprocessing')=='neutral_preserving'
               and geometry.get('pixel_preservation',{}).get('eligible')
               and geometry.get('original_source_input'))
    if geometry.get('preprocessing')=='faithful_red_signal':
        from seal_review import red_signal_preserved
        preserved=red_signal_preserved(geometry.get('red_plane_source'),response['image'])
    if geometry.get('preprocessing') not in (None,'original') and not preserved:
        result['reason']='preprocessing_integrity_unverified';return result
    # 完整字符串需有自身的序列对齐；未改字符不需要伪造编辑区间。
    selected=next((row for row in scoring.get('scores',[]) if row.get('text')==text and row.get('available')),None)
    final_tokens=tokens if text==aux else (selected or {}).get('alignment',[])
    main_selected=next((row for row in primary_scoring.get('scores',[]) if row.get('text')==text and row.get('available')),None) if dual else None
    main_alignment=(main_selected or {}).get('alignment',[])
    aligned_main=(''.join(t.get('text','') for t in main_alignment)==text and len(main_alignment)==len(final_tokens))
    support=[max(t.get('posterior',0),main_alignment[i].get('posterior',0)) if aligned_main else t.get('posterior',0)
             for i,t in enumerate(final_tokens)]
    # 已通过有限竞争的位置沿用其实际裁决证据；其余保留位置单独检查。
    # 不对同一编辑再施加另一套更高阈值，也不将局部支持扩散到相邻字符。
    verified_final_positions=set();offset=0
    for group in sorted(proof,key=lambda g:(g['start'],g['end'])):
        a,b=group['start'],group['end'];length=len(group['selected_fragment'])
        verified_final_positions.update(range(a+offset,a+offset+length))
        offset+=length-(b-a)
    final_supported=(bool(selected) and ''.join(t.get('text','') for t in final_tokens)==text
                     and bool(final_tokens) and all(i in verified_final_positions or p>=.97 for i,p in enumerate(support)))
    whole=(final_supported and evidence['eligible'] and not unresolved)
    result.update(accepted=not unresolved,recovered=text!=primary,text=text,reason='calibrated_finite_sequence_competition' if not unresolved else 'remaining_local_competition_uncertain',
                  sequence_verified=True,
                  verification_scope=dict(kind='whole_object' if whole and not unresolved else 'local_edits',
                                          source_text=primary,ranges=[[x['start'],x['end']] for x in proof]),
                  whole_sequence_review=dict(complete=whole,scope='text_line_only',selected_sequence=text,
                      selected_sequence_scored=bool(selected),all_positions_supported=final_supported,
                      selected_alignment=final_tokens,primary_selected_alignment=main_alignment,
                      verified_final_positions=sorted(verified_final_positions),
                      excludes=['undetected_text','field_relationship','other_object_roles']))
    return result
