from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict

from agent.agent_backend.feedback.optimization.prompt_critic_agent import PromptCriticAgent


class PromptTaskBuilder:
    """Build prompt-fix tasks from feedback."""

    def __init__(self) -> None:
        self.critic = PromptCriticAgent()

    def build(self, feedback_record: Dict[str, Any], root_cause: Dict[str, Any]) -> Dict[str, Any]:
        suggestion = self.suggest_prompt_patch(feedback_record, root_cause)
        version_id = f"prompt_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        return {
            "type": "prompt_fix",
            "version_id": version_id,
            "root_cause": root_cause,
            "target_templates": suggestion["target_templates"],
            "diagnosis": suggestion["diagnosis"],
            "optimization_actions": suggestion["optimization_actions"],
            "template_suffixes": suggestion["template_suffixes"],
            "bundle": {
                "prompt_version_id": version_id,
                "created_at": datetime.now().isoformat(),
                "source": "feedback_closed_loop",
                "target_templates": suggestion["target_templates"],
                "diagnosis": suggestion["diagnosis"],
                "optimization_actions": suggestion["optimization_actions"],
                "template_suffixes": suggestion["template_suffixes"],
            },
            "version_config": {
                "run_config": {
                    "prompt_config": {
                        "prompt_version_id": version_id,
                    }
                }
            },
        }

    def suggest_prompt_patch(self, feedback_record: Dict[str, Any], root_cause: Dict[str, Any]) -> Dict[str, Any]:
        return self.critic.analyze(feedback_record, root_cause)

    def persist_prompt_ticket(self, ticket: Dict[str, Any]) -> str:
        return str(ticket.get("version_id", "prompt_ticket") or "prompt_ticket")
