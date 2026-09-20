from __future__ import annotations

import logging
import re
from typing import Any


logger = logging.getLogger("agent.pre_review")


def _compact_text(value: Any, max_len: int = 160) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3].rstrip() + "..."


def _render_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        if not value:
            return ""
        parts = [_compact_text(item, max_len=48) for item in value[:3]]
        rendered = ", ".join([part for part in parts if part])
        if len(value) > 3:
            rendered = f"{rendered}, ... ({len(value)})" if rendered else f"{len(value)} items"
        return rendered
    if isinstance(value, dict):
        if not value:
            return ""
        items = []
        for idx, (key, item) in enumerate(value.items()):
            if idx >= 4:
                break
            rendered = _render_value(item)
            if rendered:
                items.append(f"{key}={rendered}")
        return "; ".join(items)
    return _compact_text(value)


def log_agent_flow(agent: str, event: str, **fields: Any) -> None:
    parts = []
    for key, value in fields.items():
        rendered = _render_value(value)
        if rendered:
            parts.append(f"{key}={rendered}")
    suffix = " | ".join(parts) if parts else "-"
    logger.info("[AgentFlow] %s.%s | %s", str(agent or "").strip(), str(event or "").strip(), suffix)
