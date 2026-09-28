"""核对首屏保留文字、对象和位置；重复候选树通过同一受权接口按需取得。"""

DETAIL_KEYS = frozenset({
    'candidate_evidence', 'role_evidence', 'automatic_adjudications',
    'diagnostic_observations', 'candidate_history', 'content_candidates',
    'seal_adjudications', 'applied_seal_adjudications', 'seal_verification',
    'authority_verification', 'sequence_evidence', 'primary_sequence_review',
    'previous_attempts', 'raw_response', 'raw_output', 'original_result',
    'regional_recognition', 'layout_region_recovery', 'dense_candidates',
    'seal_review', 'bounded_review_responses', 'original_page_result', 'page_result',
})


def compact_review_evidence(payload):
    """仅投影 API 返回；不改持久化证据，不生成新身份或保存第二份候选树。"""
    def project(value, path):
        if isinstance(value, dict):
            result = {}
            for key, child in value.items():
                pointer = path + '/' + str(key).replace('~', '~0').replace('/', '~1')
                # 来源身份是版本校验的原子值，不能投影其中的尝试诊断而改变其相等语义。
                if key == 'source_identity':
                    result[key] = child
                elif key in DETAIL_KEYS and isinstance(child, (dict, list)) and child:
                    result[key] = {'evidence_ref': pointer, 'available': True}
                else:
                    result[key] = project(child, pointer)
            return result
        if isinstance(value, list):
            return [project(child, path + '/' + str(i)) for i, child in enumerate(value)]
        return value
    result = project(payload, '')
    result['evidence_delivery'] = {'mode': 'on_demand', 'request_query': 'evidence=full',
                                 'source_identity': payload.get('source_identity')}
    return result
