from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Any

from agent.agent_backend.config.settings import _normalize_runtime_url


LLM_DIR = Path(__file__).resolve().parent


def _resolve_key_path(path_str: str) -> str:
    p = Path(path_str or "")
    if not p.is_absolute():
        p = LLM_DIR / p
    return str(p)


MODEL_CONFIG: Dict[str, Any] = {
    "active": {
        "chat": os.getenv("LLM_CHAT_MODEL", "qwen_local_chat"),
        "parse": os.getenv("LLM_PARSE_MODEL", "glm_ocr_parse"),
        "embedding": os.getenv("LLM_EMBEDDING_MODEL", "glm_embedding"),
        "rerank": os.getenv("LLM_RERANK_MODEL", "glm_rerank"),
    },
    "models": {
        # Chat models (OpenAI-compatible endpoint)
        "glm_chat": {
            "kind": "openai_chat",
            "model": "glm-4.7-flash",
            "base_url": "https://open.bigmodel.cn/api/paas/v4/",
            "api_key_path": _resolve_key_path("temp/key/glm_key"),
            "timeout": int(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
        },
        # Optional parse-by-text model (for compatibility with old prompt-based parsing)
        "glm_parse_text": {
            "kind": "openai_chat",
            "model": "glm-4.7-flash",
            "base_url": "https://open.bigmodel.cn/api/paas/v4/",
            "api_key_path": _resolve_key_path("temp/key/glm_key"),
            "timeout": int(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
        },
        # Parse model (GLM OCR)
        "deepseek_ocr": {
            "kind": "deepseek_ocr",
            "model": os.getenv("LLM_DEEPSEEK_OCR_MODEL", "deepseek-ocr"),
            "base_url": os.getenv("LLM_DEEPSEEK_OCR_BASE_URL", "https://open.deepseek.cn/api/v1"),
            "api_key_path": _resolve_key_path(os.getenv("LLM_DEEPSEEK_OCR_API_KEY_PATH", "temp/key/guiji_key")),
            "timeout": int(os.getenv("LLM_PARSE_TIMEOUT_SECONDS", "60")),
        },
        "glm_ocr_parse": {
            "kind": "glm_ocr_parse",
            "model": "glm-ocr",
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "api_key_path": _resolve_key_path("temp/key/glm_key"),
            "timeout": int(os.getenv("LLM_PARSE_TIMEOUT_SECONDS", "60")),
        },
        "qwen_chat": {
            "kind": "openai_chat",
            "model": os.getenv("LLM_QWEN_CHAT_MODEL", "qwen3.5-122b-a10b"),
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key_path": _resolve_key_path("temp/key/qwen_key"),
            "timeout": int(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
        },
        "qwen_local_chat": {
            "kind": "qwen_local_chat",
            "model": os.getenv("LLM_QWEN_LOCAL_CHAT_MODEL", "qwen3-32B"),
            "base_url": _normalize_runtime_url(
                os.getenv("LLM_QWEN_LOCAL_CHAT_BASE_URL", "http://localhost:11434/v1")
            ),
            "api_key_path": _resolve_key_path(os.getenv("LLM_QWEN_LOCAL_CHAT_API_KEY_PATH", "temp/key/local_key")),
            "timeout": int(os.getenv("LLM_QWEN_LOCAL_CHAT_TIMEOUT_SECONDS", os.getenv("LLM_TIMEOUT_SECONDS", "30"))),
        },
        "qwen_parse": {
            "kind": "openai_chat",
            "model": "qwen-turbo",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key_path": _resolve_key_path("temp/key/qwen_key"),
            "timeout": int(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
        },
        # Embedding models
        "glm_embedding": {
            "kind": "glm_embedding",
            "model": os.getenv("LLM_GLM_EMBED_MODEL", "embedding-3"),
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "api_key_path": _resolve_key_path("temp/key/glm_key"),
            "timeout": int(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
            "dimensions": os.getenv("LLM_EMBEDDING_DIMENSIONS", ""),
        },
        "hash_embedding": {
            "kind": "hash_embedding",
            "dim": int(os.getenv("LLM_HASH_EMBED_DIM", "128")),
        },
        "qwen_embedding": {
            "kind": "openai_embedding",
            "model": os.getenv("LLM_EMBEDDING_REMOTE_MODEL", "text-embedding-v3"),
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "api_key_path": _resolve_key_path("temp/key/qwen_key"),
            "timeout": int(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
        },
        # Rerank models
        "glm_rerank": {
            "kind": "glm_rerank",
            "model": os.getenv("LLM_GLM_RERANK_MODEL", "rerank"),
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "api_key_path": _resolve_key_path("temp/key/glm_key"),
            "timeout": int(os.getenv("LLM_TIMEOUT_SECONDS", "30")),
        },
        "lexical_rerank": {
            "kind": "lexical_rerank",
        },
    },
}


# Backward compatibility with old code importing `LLM`
LLM = {
    "model_use": "glm_chat",
    "models": {
        "glm": {
            "model": MODEL_CONFIG["models"]["glm_chat"]["model"],
            "base_url": MODEL_CONFIG["models"]["glm_chat"]["base_url"],
            "api_key_path": MODEL_CONFIG["models"]["glm_chat"]["api_key_path"],
        }
    },
}


def get_active_model_name(task: str) -> str:
    return str(MODEL_CONFIG.get("active", {}).get(task, "")).strip()


def get_model_conf(name: str) -> Dict[str, Any]:
    return dict(MODEL_CONFIG.get("models", {}).get(name, {}))
