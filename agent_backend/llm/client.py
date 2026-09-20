from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from agent.agent_backend.database.vector.vector_db import Embedding
from agent.agent_backend.llm.factory import ModelFactory
from agent.agent_backend.llm.model_setting import get_active_model_name
from agent.agent_backend.llm.errors import LLMContextError


def _is_verbose() -> bool:
    return os.getenv("LLM_VERBOSE_LOG", "0").strip().lower() in {"1", "true", "yes", "on"}


def _print(message: str) -> None:
    print(message)


def _debug(message: str) -> None:
    # 历史调用点可能传正文；verbose不再授予正文日志权限。
    # 请求长度、模型及阶段由安全摘要日志提供。
    return None


class LLMClient:
    """
    Unified LLM facade.
    - chat: conversational generation model
    - parse: temporarily routed to chat model only
    - embed: local Embedding from database/vector/vector_db.py
    - rerank: disabled for now
    """

    def __init__(self):
        try:
            self.chat_context_max_chars = int(os.getenv("LLM_CHAT_CONTEXT_MAX_CHARS", "200000"))
        except (TypeError, ValueError):
            raise LLMContextError("context_budget_invalid") from None
        if self.chat_context_max_chars <= 0:
            raise LLMContextError("context_budget_invalid")
        self._active_chat_model_name = get_active_model_name("chat")
        self._chat_model = ModelFactory.chat_model()
        self._embedding_model = None
        self.embedding_context_max_chars = int(os.getenv("LLM_EMBED_CONTEXT_MAX_CHARS", "8000"))
        self._log_runtime_bootstrap()

    @staticmethod
    def _compact_text(text: Any, max_len: int = 320) -> str:
        return f"[redacted chars={len(str(text or ''))}]"

    def _log_runtime_bootstrap(self) -> None:
        _print(
            "[LLM] client init | active_chat_model=%s resolved_class=%s verbose=%s"
            % (
                self._active_chat_model_name or "-",
                self._chat_model.__class__.__name__,
                _is_verbose(),
            )
        )
        if self._chat_model.__class__.__name__ == "_NullChatModel":
            _print(
                "[LLM] warning | active_chat_model=%s resolved to _NullChatModel, "
                "chat requests will return default without calling external provider"
                % (self._active_chat_model_name or "-")
            )

    @staticmethod
    def _dump_messages(messages: List[Dict[str, str]]) -> str:
        try:
            return json.dumps(messages, ensure_ascii=False, indent=2)
        except Exception:
            return str(messages)

    def _trim_messages_for_chat(self, messages: List[Dict[str, str]], stage: str = "chat") -> List[Dict[str, str]]:
        """兼容旧方法名：只核对完整输入，绝不裁掉system或必要资料。"""
        budget = self.chat_context_max_chars
        if not isinstance(budget, int) or isinstance(budget, bool) or budget <= 0:
            raise LLMContextError("context_budget_invalid", stage=stage)
        total = sum(len(str(m.get("content", ""))) for m in (messages or []))
        if total > budget:
            raise LLMContextError(actual_chars=total, limit_chars=budget, stage=stage)
        return [dict(m) for m in (messages or [])]

    def _trim_text_for_embedding(self, text: str) -> str:
        if self.embedding_context_max_chars <= 0:
            return text or ""
        return (text or "")[: self.embedding_context_max_chars]

    def _get_embedding_model(self) -> Embedding:
        if self._embedding_model is None:
            self._embedding_model = Embedding()
        return self._embedding_model

    def chat(self, messages: List[Dict[str, str]], default: str = "") -> str:
        trimmed = self._trim_messages_for_chat(messages)
        _print(
            "[LLM] chat start | model=%s class=%s message_count=%s input_chars=%s stage=chat"
            % (
                self._active_chat_model_name or "-",
                self._chat_model.__class__.__name__,
                len(trimmed or []),
                sum(len(str(m.get('content', ''))) for m in trimmed),
            )
        )
        result = self._chat_model.chat(messages=trimmed, default=default)
        if result == default:
            _print(
                "[LLM] chat fallback | model=%s class=%s output_is_default=true output_preview=%s"
                % (
                    self._active_chat_model_name or "-",
                    self._chat_model.__class__.__name__,
                    self._compact_text(result),
                )
            )
        else:
            _print(
                "[LLM] chat success | model=%s class=%s output_preview=%s"
                % (
                    self._active_chat_model_name or "-",
                    self._chat_model.__class__.__name__,
                    self._compact_text(result),
                )
            )
        return result

    def parse(
        self,
        messages: Optional[List[Dict[str, str]]] = None,
        file: str = "",
        default: str = "",
    ) -> str:
        trimmed = self._trim_messages_for_chat(messages or [], stage="parse")
        _print(
            "[LLM] parse start | model=%s class=%s message_count=%s file=%s input_chars=%s stage=parse"
            % (
                self._active_chat_model_name or "-",
                self._chat_model.__class__.__name__,
                len(trimmed or []),
                bool(file),
                sum(len(str(m.get('content', ''))) for m in trimmed),
            )
        )
        if file:
            _debug("[LLMDebug] parse.note: dedicated parse model disabled, file parsing skipped")
            return default
        result = self._chat_model.chat(messages=trimmed, default=default)
        if result == default:
            _print(
                "[LLM] parse fallback | model=%s class=%s output_is_default=true output_preview=%s"
                % (
                    self._active_chat_model_name or "-",
                    self._chat_model.__class__.__name__,
                    self._compact_text(result),
                )
            )
        else:
            _print(
                "[LLM] parse success | model=%s class=%s output_preview=%s"
                % (
                    self._active_chat_model_name or "-",
                    self._chat_model.__class__.__name__,
                    self._compact_text(result),
                )
            )
        return result

    def parse_layout(self, file: str) -> Dict[str, Any]:
        _debug(f"[LLMDebug] parse_layout.input.file: {file}")
        _debug("[LLMDebug] parse_layout.note: disabled")
        return {}

    def embed(self, text: str, dimensions: Optional[int] = None) -> List[float]:
        safe_text = self._trim_text_for_embedding(text)
        _debug(f"[LLMDebug] embed.input.dimensions: {dimensions}")
        _debug("[LLMDebug] embed.input.text:")
        _debug(safe_text)
        _ = dimensions
        result = self._get_embedding_model().embed_query(safe_text)
        _debug(f"[LLMDebug] embed.output.vector_dim: {len(result)}")
        return result

    def batch_embed(self, texts: List[str]) -> List[List[float]]:
        _debug(f"[LLMDebug] batch_embed.input.count: {len(texts or [])}")
        _debug("[LLMDebug] batch_embed.input.texts:")
        _debug(json.dumps(texts or [], ensure_ascii=False, indent=2))
        result = self._get_embedding_model().convert_text_to_embedding(texts or [])
        _debug(f"[LLMDebug] batch_embed.output.count: {len(result or [])}")
        return result

    def rerank(self, query: str, documents: List[str], top_n: Optional[int] = None) -> List[float]:
        _debug(f"[LLMDebug] rerank.input.query: {query}")
        _debug(f"[LLMDebug] rerank.input.top_n: {top_n}")
        _debug("[LLMDebug] rerank.input.documents:")
        _debug(json.dumps(documents or [], ensure_ascii=False, indent=2))
        result = [0.0 for _ in (documents or [])]
        _debug("[LLMDebug] rerank.output.scores:")
        _debug(json.dumps(result or [], ensure_ascii=False))
        return result

    @staticmethod
    def extract_json(raw_text: str) -> Optional[Any]:
        if raw_text is None:
            return None
        text = str(raw_text).strip()
        if not text:
            return None

        def _strip_code_fence(value: str) -> str:
            out = value.strip()
            if out.startswith("```"):
                out = re.sub(r"^```(?:json)?\s*", "", out, flags=re.IGNORECASE)
                out = re.sub(r"\s*```$", "", out)
            return out.strip()

        def _cleanup_common_json_artifacts(value: str) -> str:
            # 轻量清洗：处理模型偶发的全角引号、BOM、尾逗号。
            out = value.lstrip("\ufeff").strip()
            out = out.replace("“", "\"").replace("”", "\"").replace("‘", "'").replace("’", "'")
            out = re.sub(r",\s*([}\]])", r"\1", out)
            return out

        def _try_load(value: str) -> Optional[Any]:
            try:
                return json.loads(value)
            except Exception:
                return None

        def _find_balanced_json_segments(value: str, max_segments: int = 8) -> List[str]:
            segments: List[str] = []
            stack: List[str] = []
            start_idx = -1
            in_string = False
            escape = False
            for idx, ch in enumerate(value):
                if in_string:
                    if escape:
                        escape = False
                    elif ch == "\\":
                        escape = True
                    elif ch == "\"":
                        in_string = False
                    continue
                if ch == "\"":
                    in_string = True
                    continue
                if ch in "{[":
                    if not stack:
                        start_idx = idx
                    stack.append(ch)
                    continue
                if ch in "}]":
                    if not stack:
                        continue
                    last = stack[-1]
                    if (last == "{" and ch == "}") or (last == "[" and ch == "]"):
                        stack.pop()
                        if not stack and start_idx >= 0:
                            segment = value[start_idx: idx + 1].strip()
                            if segment:
                                segments.append(segment)
                                if len(segments) >= max_segments:
                                    break
                            start_idx = -1
                    else:
                        stack.clear()
                        start_idx = -1
            return segments

        candidates: List[str] = []
        direct = _strip_code_fence(text)
        if direct:
            candidates.append(direct)
        candidates.extend(_find_balanced_json_segments(text))

        # 兜底：保留旧逻辑的正则提取，但改为非贪婪，避免跨段吞并。
        for m in re.finditer(r"(\{[\s\S]*?\}|\[[\s\S]*?\])", text):
            snippet = str(m.group(1) or "").strip()
            if snippet:
                candidates.append(snippet)

        seen = set()
        for item in candidates:
            if not item or item in seen:
                continue
            seen.add(item)
            parsed = _try_load(item)
            if parsed is not None:
                return parsed
            cleaned = _cleanup_common_json_artifacts(_strip_code_fence(item))
            if cleaned and cleaned != item:
                parsed = _try_load(cleaned)
                if parsed is not None:
                    return parsed
        return None
