import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


def _load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
_load_env_file(ROOT / ".env")


def _running_in_container() -> bool:
    if os.path.exists("/.dockerenv"):
        return True
    cgroup_path = Path("/proc/1/cgroup")
    try:
        if cgroup_path.exists():
            content = cgroup_path.read_text(encoding="utf-8", errors="ignore").lower()
            if "docker" in content or "kubepods" in content or "containerd" in content:
                return True
    except Exception:
        return False
    return False


def _runtime_alias_map() -> dict[str, str]:
    return {
        "mysql": "MYSQL_LOCAL_FALLBACK_HOST",
        "milvus": "MILVUS_LOCAL_FALLBACK_HOST",
        "ocr-service": "OCR_LOCAL_FALLBACK_HOST",
        "host.docker.internal": "HOST_DOCKER_INTERNAL_FALLBACK_HOST",
    }


def _normalize_runtime_host(host: str, default_fallback: str = "127.0.0.1") -> str:
    value = str(host or "").strip()
    if not value or _running_in_container():
        return value
    env_key = _runtime_alias_map().get(value.lower())
    if not env_key:
        return value
    return str(os.getenv(env_key, default_fallback) or "").strip() or default_fallback


def _normalize_runtime_url(url: str, default_fallback: str = "127.0.0.1") -> str:
    value = str(url or "").strip()
    if not value or "://" not in value:
        return value
    parts = urlsplit(value)
    if not parts.netloc or not parts.hostname:
        return value
    normalized_host = _normalize_runtime_host(parts.hostname, default_fallback=default_fallback)
    if normalized_host == parts.hostname:
        return value
    netloc = normalized_host
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    if parts.username:
        auth = parts.username
        if parts.password:
            auth = f"{auth}:{parts.password}"
        netloc = f"{auth}@{netloc}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def _resolve_data_path(path_str: str, default_path: Path) -> str:
    value = (path_str or "").strip()
    if not value:
        return str(default_path)
    p = Path(value)
    if p.is_absolute():
        return str(p)
    normalized = value.replace("\\", "/")
    if normalized.startswith("agent/"):
        return str(REPO_ROOT / p)
    return str(ROOT / p)


def _resolve_mysql_url(url: str) -> str:
    value = (url or "").strip()
    if not value.startswith("sqlite:///"):
        return _normalize_runtime_url(value)
    raw_path = value.replace("sqlite:///", "", 1).strip()
    p = Path(raw_path)
    if p.is_absolute():
        return value
    normalized = raw_path.replace("\\", "/")
    if normalized.startswith("agent/"):
        return f"sqlite:///{(REPO_ROOT / p).as_posix()}"
    return f"sqlite:///{(ROOT / p).as_posix()}"


def _resolve_vector_uri(uri: str, default_path: Path) -> str:
    value = (uri or "").strip()
    if not value:
        return "http://127.0.0.1:19530"
    if value.startswith("http://") or value.startswith("https://"):
        return _normalize_runtime_url(value)
    return _resolve_data_path(value, default_path)


@dataclass
class Settings:
    app_host: str = os.getenv("APP_HOST", "0.0.0.0")
    app_port: int = int(os.getenv("APP_PORT", "5002"))
    debug: bool = os.getenv("DEBUG", "true").lower() == "true"
    auto_start_llm: bool = os.getenv("AUTO_START_LLM", "true").lower() == "true"
    llm_host: str = os.getenv("LLM_HOST", "127.0.0.1")
    llm_port: int = int(os.getenv("LLM_PORT", "8024"))

    upload_dir: str = _resolve_data_path(
        os.getenv("UPLOAD_DIR", ""),
        ROOT / "data" / "uploads",
    )
    parse_dir: str = _resolve_data_path(
        os.getenv("PARSE_DIR", ""),
        ROOT / "data" / "parsed",
    )
    submission_upload_dir: str = _resolve_data_path(
        os.getenv("SUBMISSION_UPLOAD_DIR", ""),
        ROOT / "data" / "submissions" / "uploads",
    )
    submission_parse_dir: str = _resolve_data_path(
        os.getenv("SUBMISSION_PARSE_DIR", ""),
        ROOT / "data" / "submissions" / "parsed",
    )
    report_dir: str = _resolve_data_path(
        os.getenv("REPORT_DIR", ""),
        ROOT / "data" / "reports",
    )
    feedback_asset_dir: str = _resolve_data_path(
        os.getenv("FEEDBACK_ASSET_DIR", ""),
        ROOT / "data" / "feedback_assets",
    )
    rule_data_dir: str = _resolve_data_path(
        os.getenv("RULE_DATA_DIR", ""),
        ROOT / "data" / "rule",
    )
    vector_snapshot_dir: str = _resolve_data_path(
        os.getenv("VECTOR_SNAPSHOT_DIR", ""),
        ROOT / "data" / "vector_snapshots",
    )

    mysql_url: str = _resolve_mysql_url(
        os.getenv("MYSQL_URL", f"sqlite:///{(ROOT / 'data' / 'agent.db').as_posix()}")
    )
    redis_host: str = _normalize_runtime_host(os.getenv("REDIS_HOST", "127.0.0.1"))
    redis_port: int = int(os.getenv("REDIS_PORT", "6379"))
    redis_db: int = int(os.getenv("REDIS_DB", "0"))

    es_url: str = _normalize_runtime_url(os.getenv("ES_URL", "http://127.0.0.1:9200"))
    vector_collection: str = os.getenv("VECTOR_COLLECTION", "agent_collection")
    vector_backend: str = os.getenv("VECTOR_BACKEND", "milvus")
    vector_db_uri: str = _resolve_vector_uri(
        os.getenv("MILVUS_URI", ""),
        ROOT / "data" / "milvus" / "agent_milvus.db",
    )
    milvus_token: str = os.getenv("MILVUS_TOKEN", "")
    milvus_db_name: str = os.getenv("MILVUS_DB_NAME", "")
    kb_chunk_max_chars: int = int(os.getenv("KB_CHUNK_MAX_CHARS", "1200"))
    kb_chunk_overlap: int = int(os.getenv("KB_CHUNK_OVERLAP", "120"))
    kb_section_path_sep: str = os.getenv("KB_SECTION_PATH_SEP", " > ")
    kb_parse_workers: int = int(os.getenv("KB_PARSE_WORKERS", "2"))
    kb_progress_max_entries: int = int(os.getenv("KB_PROGRESS_MAX_ENTRIES", "2000"))
    kb_progress_ttl_seconds: int = int(os.getenv("KB_PROGRESS_TTL_SECONDS", "7200"))

    ocr_service_url: str = _normalize_runtime_url(os.getenv("OCR_SERVICE_URL", "http://127.0.0.1:8000"))
    ocr_timeout_seconds: int = int(os.getenv("OCR_TIMEOUT_SECONDS", "120"))
    ocr_lang: str = os.getenv("OCR_LANG", "chi_sim+eng")

    llm_chat_model: str = os.getenv("LLM_CHAT_MODEL", "qwen_local_chat")
    llm_parse_model: str = os.getenv("LLM_PARSE_MODEL", "glm_ocr_parse")
    llm_embedding_model: str = os.getenv("LLM_EMBEDDING_MODEL", "glm_embedding")
    llm_rerank_model: str = os.getenv("LLM_RERANK_MODEL", "glm_rerank")
    llm_timeout_seconds: int = int(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
    llm_parse_timeout_seconds: int = int(os.getenv("LLM_PARSE_TIMEOUT_SECONDS", "60"))
    llm_verbose_log: bool = os.getenv("LLM_VERBOSE_LOG", "0").lower() in {"1", "true", "yes", "on"}
    llm_chat_context_max_chars: int = int(os.getenv("LLM_CHAT_CONTEXT_MAX_CHARS", "200000"))
    llm_embed_context_max_chars: int = int(os.getenv("LLM_EMBED_CONTEXT_MAX_CHARS", "8000"))
    llm_qwen_local_chat_model: str = os.getenv("LLM_QWEN_LOCAL_CHAT_MODEL", "qwen3-32B")
    llm_qwen_local_chat_base_url: str = _normalize_runtime_url(
        os.getenv("LLM_QWEN_LOCAL_CHAT_BASE_URL", "http://localhost:11434/v1")
    )
    llm_qwen_local_chat_api_key_path: str = os.getenv("LLM_QWEN_LOCAL_CHAT_API_KEY_PATH", "temp/key/local_key")
    llm_qwen_local_chat_timeout_seconds: int = int(
        os.getenv("LLM_QWEN_LOCAL_CHAT_TIMEOUT_SECONDS", os.getenv("LLM_TIMEOUT_SECONDS", "30"))
    )


settings = Settings()
