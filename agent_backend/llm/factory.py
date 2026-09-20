from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Dict

from agent.agent_backend.llm.base import ChatModelBase, EmbeddingModelBase, ParseModelBase, RerankModelBase
from agent.agent_backend.llm.model_setting import get_active_model_name, get_model_conf
from agent.agent_backend.llm.providers.glm_models import GLMEmbeddingModel, GLMOCRParseModel, GLMRerankModel
from agent.agent_backend.llm.providers.local_models import (
    HashEmbeddingModel,
    HashRerankModel,
    LexicalRerankModel,
    OpenAICompatibleEmbeddingModel,
)
from agent.agent_backend.llm.providers.openai_compatible import OpenAICompatibleTextModel


logger = logging.getLogger("agent.llm.factory")


def _warn(message: str) -> None:
    logger.warning(message)
    print(message)


class _NullChatModel(ChatModelBase, ParseModelBase):
    def chat(self, messages, default: str = "") -> str:
        _warn("[LLM] _NullChatModel.chat invoked, request skipped and default returned")
        return default

    def parse(self, messages=None, file: str = "", default: str = "") -> str:
        _warn("[LLM] _NullChatModel.parse invoked, request skipped and default returned")
        return default


class _NullEmbeddingModel(EmbeddingModelBase):
    def embed(self, text: str, dimensions=None):
        return []


class _NullRerankModel(RerankModelBase):
    def rerank(self, query: str, documents, top_n=None):
        return [0.0 for _ in documents]


class ModelFactory:
    @staticmethod
    def _build_model(name: str, conf: Dict[str, Any]):
        kind = str(conf.get("kind", "")).strip().lower()
        if kind in {"openai_chat", "qwen_local_chat"}:
            return OpenAICompatibleTextModel(conf)
        if kind == "openai_embedding":
            return OpenAICompatibleEmbeddingModel(conf)
        if kind == "glm_embedding":
            return GLMEmbeddingModel(conf)
        if kind == "hash_embedding":
            return HashEmbeddingModel(conf)
        if kind == "glm_ocr_parse":
            return GLMOCRParseModel(conf)
        if kind == "glm_rerank":
            return GLMRerankModel(conf)
        if kind == "lexical_rerank":
            return LexicalRerankModel()
        if kind == "hash_rerank":
            return HashRerankModel()
        _warn(
            "[LLM] unsupported model kind | model_name=%s kind=%s conf_keys=%s"
            % (name or "-", kind or "-", sorted((conf or {}).keys()))
        )
        return None

    @classmethod
    @lru_cache(maxsize=64)
    def by_name(cls, model_name: str):
        conf = get_model_conf(model_name)
        if not conf:
            _warn("[LLM] model config not found | model_name=%s" % (model_name or "-"))
            return None
        model = cls._build_model(model_name, conf)
        print(
            "[LLM] factory resolved | model_name=%s kind=%s resolved_class=%s"
            % (
                model_name or "-",
                str(conf.get("kind", "") or "-"),
                model.__class__.__name__ if model is not None else "None",
            )
        )
        return model

    @classmethod
    def chat_model(cls) -> ChatModelBase:
        active_name = get_active_model_name("chat")
        model = cls.by_name(active_name)
        if isinstance(model, ChatModelBase):
            return model
        _warn(
            "[LLM] chat model fallback to _NullChatModel | active_name=%s resolved_class=%s"
            % (active_name or "-", model.__class__.__name__ if model is not None else "None")
        )
        return _NullChatModel()

    @classmethod
    def parse_model(cls) -> ParseModelBase:
        active_name = get_active_model_name("parse")
        model = cls.by_name(active_name)
        if isinstance(model, ParseModelBase):
            return model
        _warn(
            "[LLM] parse model fallback to _NullChatModel | active_name=%s resolved_class=%s"
            % (active_name or "-", model.__class__.__name__ if model is not None else "None")
        )
        return _NullChatModel()

    @classmethod
    def embedding_model(cls) -> EmbeddingModelBase:
        model = cls.by_name(get_active_model_name("embedding"))
        return model if isinstance(model, EmbeddingModelBase) else _NullEmbeddingModel()

    @classmethod
    def rerank_model(cls) -> RerankModelBase:
        model = cls.by_name(get_active_model_name("rerank"))
        return model if isinstance(model, RerankModelBase) else _NullRerankModel()
