"""章节必要输出校验；不以默认值补造缺失的审评结论。"""
from agent.agent_backend.llm.errors import LLMExecutionError


def require_object(value, stage):
    if not isinstance(value, dict):
        raise LLMExecutionError('model_invalid_json', stage=stage)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def text_list(value):
    return isinstance(value, list) and all(text(item) for item in value)


def validate_feedback_analysis(value, taxonomy):
    stage = 'feedback_analyzer'
    require_object(value, stage)
    if (value.get('feedback_polarity') not in ('positive', 'negative', 'partial')
            or not text(value.get('root_cause'))
            or any(not text_list(value.get(key)) for key in ('error_types', 'attention_points_next_time', 'retrieval_missed'))
            or any(item not in taxonomy for item in value['error_types'])
            or not isinstance(value.get('new_experience'), list)
            or any(not isinstance(item, dict) or not text(item.get('content')) for item in value['new_experience'])):
        raise LLMExecutionError('model_invalid_output', stage=stage)


def validate_feedback_patch(value, patch_types, allowed_targets, normalize_target):
    stage = 'feedback_optimizer'
    require_object(value, stage)
    if (not isinstance(value.get('patches'), list)
            or not isinstance(value.get('candidate_templates'), dict)
            or any(not isinstance(v, str) for v in value['candidate_templates'].values())
            or not text_list(value.get('applicable_conditions'))):
        raise LLMExecutionError('model_invalid_output', stage=stage)
    for item in value['patches']:
        if (not isinstance(item, dict) or not text(item.get('patch_type')) or item['patch_type'] not in patch_types
                or not text(item.get('patch_content'))
                or normalize_target(item.get('target_agent')) not in allowed_targets(item['patch_type'])):
            raise LLMExecutionError('model_invalid_output', stage=stage)


def validate_reflection(value):
    stage = 'meta_reflector'
    require_object(value, stage)
    if (not text(value.get('reflection_summary')) or type(value.get('bad_case')) is not bool
            or not text_list(value.get('high_frequency_doc_ids'))
            or not isinstance(value.get('distilled_experiences'), list)
            or any(not isinstance(item, dict) or not text(item.get('content')) for item in value['distilled_experiences'])
            or not isinstance(value.get('few_shot_example'), dict)):
        raise LLMExecutionError('model_invalid_output', stage=stage)
    example = value['few_shot_example']
    if (not isinstance(example.get('title'), str)
            or any(not isinstance(example.get(key), dict) for key in ('input_snapshot', 'expected_output'))):
        raise LLMExecutionError('model_invalid_output', stage=stage)


def validate_plan_output(value):
    stage = 'chapter_planner'
    require_object(value, stage)
    if (not text_list(value.get('query_list')) or not isinstance(value.get('retrieval_plan'), list)
            or not isinstance(value.get('review_tasks'), list)
            or any(not isinstance(row, dict) or not all(text(row.get(key)) for key in ('source_type','purpose'))
                   or not text_list(row.get('query_subset')) for row in value['retrieval_plan'])
            or any(not isinstance(row, dict) or not all(text(row.get(key)) for key in ('task_code','task_question','review_object'))
                   for row in value['review_tasks'])):
        raise LLMExecutionError('model_invalid_output', stage=stage)


def validate_review_output(value, stage):
    require_object(value, stage)
    statuses = ('supported', 'unsupported', 'insufficient_information')
    if (not text(value.get('section_summary')) or value.get('pre_review_conclusion') not in statuses
            or any(not text_list(value.get(key)) for key in ('supported_points','unsupported_points','missing_points','risk_points'))
            or not isinstance(value.get('task_verdicts'), list)):
        raise LLMExecutionError('model_invalid_output', stage=stage)
    seen = set()
    for row in value['task_verdicts']:
        if (not isinstance(row, dict) or not text(row.get('task_code'))
                or row.get('status') not in statuses or not text(row.get('reason'))
                or not text(row.get('basis')) or row['task_code'] in seen):
            raise LLMExecutionError('model_invalid_output', stage=stage)
        seen.add(row['task_code'])
    # 明确支持不能没有任何支持依据，不能由后处理补造“已审查”。
    if (value['pre_review_conclusion'] == 'supported' and not value['supported_points']
            and not any(row['status'] == 'supported' for row in value['task_verdicts'])):
        raise LLMExecutionError('model_invalid_output', stage=stage)
