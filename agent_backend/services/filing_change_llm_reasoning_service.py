import json
from typing import Any, Dict, List

from agent.agent_backend.llm.errors import LLMExecutionError


class FilingChangeLLMReasoningService:
    """AI 审评 LLM 推理服务：用于核心综合判断和草稿文本生成。"""

    def __init__(self) -> None:
        from agent.agent_backend.llm.client import LLMClient
        self.llm = LLMClient()

    def _call_json(self, system_prompt: str, user_payload: Dict[str, Any], default_obj: Dict[str, Any]) -> Dict[str, Any]:
        default_text = json.dumps(default_obj, ensure_ascii=False)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ]
        raw = self.llm.chat(messages=messages, default=default_text)
        parsed = self.llm.extract_json(raw)
        if isinstance(parsed, dict):
            return parsed
        raise LLMExecutionError('model_invalid_json', stage='filing_reasoning')

    def suggest_change_category(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        default_obj = {
            "suggested_category": "需人工确认",
            "confidence": 0.55,
            "reason": "资料不足或规则冲突，建议人工复核。",
            "risk_points": [],
            "evidence": [],
            "need_manual_review": True,
        }
        prompt = (
            "你是药品备案变更审评辅助智能体。请基于输入的结构化结果，输出变更管理类别初步建议。"
            "结论必须是 AI 初步建议，不得输出最终审批决定；资料不足时输出需人工确认。仅输出 JSON。"
        )
        return self._call_json(prompt, payload, default_obj)

    def summarize_overall(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self._call_json(
            '仅安排输入facts事实句的解释顺序。返回JSON对象，仅有explanation_order字段，值为全部事实句的零起始索引排列；不得遗漏、重复、添加文字、结论或证据。',
            payload, {})

    def generate_correction_notice(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        default_obj = {
            "title": "补正通知书草稿",
            "items": [],
            "draft_text": "请按缺失项与不一致项补充资料并重新提交。",
        }
        prompt = (
            "你是药品审评补正通知书起草助手。根据输入问题清单、风险点和证据来源，生成补正通知书草稿。"
            "不得加入输入中没有的问题。仅输出 JSON。"
        )
        return self._call_json(prompt, payload, default_obj)

    def generate_review_report(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        default_obj = {
            "report_title": "药品备案变更审评报告草稿",
            "sections": [],
            "ai_preliminary_conclusion": "AI 初步建议：需人工确认",
            "manual_review_placeholder": "【审评员复核意见】",
        }
        prompt = (
            "你是药品备案变更审评报告起草助手。请根据输入的结构化审评结果生成报告草稿，"
            "结论必须表述为 AI 初步建议，不得输出最终审批决定。仅输出 JSON。"
        )
        return self._call_json(prompt, payload, default_obj)
