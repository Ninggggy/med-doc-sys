from pathlib import Path
import sys
import os
import atexit
import socket
import subprocess
import importlib.util
import logging

from flask import Flask, jsonify
from werkzeug.exceptions import RequestEntityTooLarge

# Support direct execution: `python agent/agent_backend/app.py`
# by injecting the project root into sys.path before absolute imports.
if __package__ is None or __package__ == "":
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

from agent.agent_backend.config.settings import settings
from agent.agent_backend.controller import register_controllers
from agent.agent_backend.services.ctd_section_bootstrap_service import CTDSectionBootstrapService
from agent.agent_backend.llm.model_setting import MODEL_CONFIG

_llm_process: subprocess.Popen | None = None


def _is_port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def _should_boot_llm() -> bool:
    if os.getenv("AUTO_START_LLM", "true").lower() != "true":
        return False
    if settings.debug:
        # avoid duplicate child process when reloader forks.
        return os.environ.get("WERKZEUG_RUN_MAIN") == "true"
    return True


def _boot_llm_server() -> None:
    global _llm_process
    if not _should_boot_llm():
        return
    llm_host = os.getenv("LLM_HOST", "127.0.0.1")
    llm_port = int(os.getenv("LLM_PORT", "8024"))
    if importlib.util.find_spec("uvicorn") is None:
        logging.warning("AUTO_START_LLM skipped: uvicorn is not installed in current environment.")
        return
    if _is_port_open(llm_host, llm_port):
        return
    root = Path(__file__).resolve().parents[2]
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "agent.agent_backend.llm.llm_server:app",
        "--host",
        llm_host,
        "--port",
        str(llm_port),
    ]
    _llm_process = subprocess.Popen(cmd, cwd=str(root))

    def _cleanup() -> None:
        global _llm_process
        if _llm_process and _llm_process.poll() is None:
            _llm_process.terminate()
        _llm_process = None

    atexit.register(_cleanup)


def _bootstrap_ctd_sections() -> None:
    try:
        CTDSectionBootstrapService().bootstrap()
    except Exception as exc:
        logging.exception("CTD section bootstrap failed during app startup: %s", str(exc))


def create_app() -> Flask:
    app = Flask(__name__)
    max_content_length_mb = int(os.getenv("MAX_CONTENT_LENGTH_MB", "512"))
    app.config["MAX_CONTENT_LENGTH"] = max_content_length_mb * 1024 * 1024
    logging.warning(
        "LLM runtime bootstrap | active=%s verbose=%s host=%s port=%s",
        MODEL_CONFIG.get("active", {}),
        os.getenv("LLM_VERBOSE_LOG", "0"),
        os.getenv("LLM_HOST", "127.0.0.1"),
        os.getenv("LLM_PORT", "8024"),
    )
    _boot_llm_server()
    _bootstrap_ctd_sections()
    register_controllers(app)

    @app.errorhandler(RequestEntityTooLarge)
    def _handle_request_entity_too_large(_exc):
        return (
            jsonify(
                {
                    "success": False,
                    "message": f"上传文件过大，当前请求体上限为 {max_content_length_mb}MB",
                }
            ),
            413,
        )

    @app.after_request
    def _set_keep_alive_headers(resp):
        resp.headers["Connection"] = "keep-alive"
        resp.headers["Keep-Alive"] = "timeout=120, max=1000"
        return resp

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host=settings.app_host, port=settings.app_port, debug=settings.debug)
