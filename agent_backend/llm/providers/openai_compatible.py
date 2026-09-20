from __future__ import annotations

import json
import logging
import os
import re
import time
from urllib.parse import urlsplit
from pathlib import Path
from typing import Any, Dict, List, Optional

from openai import OpenAI

from agent.agent_backend.llm.base import ChatModelBase, ParseModelBase
from agent.agent_backend.llm.errors import LLMContextError, LLMExecutionError


logger = logging.getLogger("agent.llm.openai_compatible")


def _is_verbose() -> bool:
    return os.getenv("LLM_VERBOSE_LOG", "0").strip().lower() in {"1", "true", "yes", "on"}


def _print(message: str) -> None:
    print(message)


def _warn(message: str) -> None:
    logger.warning(message)
    _print(message)


def _debug(message: str) -> None:
    return None


def _compact_text(text: Any, max_len: int = 320) -> str:
    return f"[redacted chars={len(str(text or ''))}]"


def _safe_endpoint(value: str) -> str:
    try:
        parts = urlsplit(value)
        return f"{parts.scheme}://{parts.hostname or '-'}"  # 不输出路径、认证、查询参数。
    except ValueError:
        return "invalid-endpoint"


def _dump_messages(messages: List[Dict[str, str]]) -> str:
    try:
        return json.dumps(messages, ensure_ascii=False, indent=2)
    except Exception:
        return str(messages)


def _read_api_key(conf: Dict[str, Any]) -> str:
    p = Path(str(conf.get("api_key_path", "") or ""))
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    return ""


def _is_local_base_url(base_url: str) -> bool:
    value = str(base_url or "").strip().lower()
    return value.startswith("http://localhost") or value.startswith("http://127.0.0.1") or value.startswith("http://0.0.0.0")


class OpenAICompatibleTextModel(ChatModelBase, ParseModelBase):
    def __init__(self, conf: Dict[str, Any]):
        self.model = str(conf.get("model", ""))
        self.base_url = str(conf.get("base_url", "")).strip()
        self.timeout = int(conf.get("timeout", 30))
        self.api_key = _read_api_key(conf)
        if not self.api_key and _is_local_base_url(self.base_url):
            self.api_key = "local-placeholder-key"
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout)
        _print(
            "[LLM] provider init | class=OpenAICompatibleTextModel model=%s base_url=%s has_api_key=%s timeout=%s"
            % (self.model or "-", _safe_endpoint(self.base_url), bool(self.api_key), self.timeout)
        )

    def _run(self, messages: List[Dict[str, str]], default: str = "") -> str:
        started = time.monotonic()
        _print(
            "[LLM] request start | provider=openai_compatible model=%s base_url=%s message_count=%s"
            % (self.model or "-", _safe_endpoint(self.base_url), len(messages or []))
        )
        _debug("[LLMDebug] provider.input.messages:")
        _debug(_dump_messages(messages or []))
        if not self.model or not self.base_url or (not self.api_key and not _is_local_base_url(self.base_url)):
            _warn(
                "[LLM] request skipped | model=%s has_model=%s has_api_key=%s has_base_url=%s"
                % (
                    self.model or "-",
                    bool(self.model),
                    bool(self.api_key),
                    bool(self.base_url),
                )
            )
            raise LLMExecutionError("model_configuration_invalid")
        try:
            resp = self.client.chat.completions.create(model=self.model, messages=messages)
            content = resp.choices[0].message.content if resp and resp.choices else ""
            if not isinstance(content, str):
                raise LLMExecutionError("model_empty_output" if content is None else "model_invalid_output")
            result = content.strip()
            if not result:
                raise LLMExecutionError("model_empty_output")
            _print(
                "[LLM] request success | model=%s output_chars=%s elapsed_seconds=%.3f"
                % (self.model or "-", len(result), time.monotonic() - started)
            )
            _debug("[LLMDebug] provider.output.raw:")
            _debug(_compact_text(result, max_len=4000))
            return result
        except (LLMContextError, LLMExecutionError):
            raise
        except Exception as exc:
            code = str(getattr(exc, "code", "") or "")
            body = getattr(exc, "body", None)
            if isinstance(body, dict):
                error = body.get("error", body)
                if isinstance(error, dict):
                    code = str(error.get("code", code) or code)
            # 可检查服务信息以分类，但绝不写入日志或异常链。
            is_context = code in {"context_length_exceeded", "max_tokens_exceeded", "context_window_exceeded"}
            if getattr(exc, "status_code", None) in {400, 413, 422}:
                is_context = is_context or bool(re.search(r"context.{0,30}(length|window)|maximum.{0,20}tokens", str(exc), re.I))
            if is_context:
                raise LLMContextError("provider_context_limit") from None
            _warn(
                "[LLM] request failed | model=%s message_count=%s error_type=%s http_status=%s elapsed_seconds=%.3f"
                % (
                    self.model or "-",
                    len(messages or []),
                    type(exc).__name__,
                    getattr(exc, "status_code", None),
                    time.monotonic() - started,
                )
            )
            status = getattr(exc, "status_code", None)
            error_type = type(exc).__name__
            if status in {401, 403}:
                reason = "model_auth_failed"
            elif status == 429:
                reason = "model_rate_limited"
            elif isinstance(status, int) and status >= 500:
                reason = "model_service_error"
            elif isinstance(exc, TimeoutError) or error_type == "APITimeoutError":
                reason = "model_timeout"
            elif isinstance(exc, ConnectionError) or error_type == "APIConnectionError":
                reason = "model_connection_failed"
            else:
                reason = "model_call_failed"
            raise LLMExecutionError(reason, http_status=status,
                                    retryable=reason in {"model_rate_limited", "model_service_error", "model_timeout", "model_connection_failed"}) from None

    def chat(self, messages: List[Dict[str, str]], default: str = "") -> str:
        return self._run(messages=messages, default=default)

    def parse(
        self,
        messages: Optional[List[Dict[str, str]]] = None,
        file: str = "",
        default: str = "",
    ) -> str:
        if not messages:
            return default
        return self._run(messages=messages, default=default)
