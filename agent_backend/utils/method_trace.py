import inspect
import os
import sys
import threading
from typing import Any


_trace_state = threading.local()
_enabled = False

_TRACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_TARGET_DIRS = {
    os.path.join(_TRACE_ROOT, "utils"),
    os.path.join(_TRACE_ROOT, "services"),
    os.path.join(_TRACE_ROOT, "controller"),
}


def _safe_repr(value: Any, max_len: int = 180) -> str:
    print("[DEBUG] enter _safe_repr | core:", {k: ((v[:100] + "...") if isinstance(v, str) and len(v) > 100 else v) for k, v in locals().items() if k in ("project_id", "doc_id", "file_path", "file_name", "file_type", "classification", "section_id", "run_id", "query", "page", "page_size", "status", "chunk_id")})
    try:
        text = repr(value)
    except Exception:
        text = f"<unrepr {type(value).__name__}>"
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


def _locals_snapshot(frame, max_items: int = 10) -> str:
    print("[DEBUG] enter _locals_snapshot | core:", {k: ((v[:100] + "...") if isinstance(v, str) and len(v) > 100 else v) for k, v in locals().items() if k in ("project_id", "doc_id", "file_path", "file_name", "file_type", "classification", "section_id", "run_id", "query", "page", "page_size", "status", "chunk_id")})
    parts = []
    for idx, (k, v) in enumerate(frame.f_locals.items()):
        if idx >= max_items:
            parts.append("...")
            break
        parts.append(f"{k}={_safe_repr(v)}")
    return ", ".join(parts)


def _qualname(frame) -> str:
    print("[DEBUG] enter _qualname | core:", {k: ((v[:100] + "...") if isinstance(v, str) and len(v) > 100 else v) for k, v in locals().items() if k in ("project_id", "doc_id", "file_path", "file_name", "file_type", "classification", "section_id", "run_id", "query", "page", "page_size", "status", "chunk_id")})
    module_name = frame.f_globals.get("__name__", "")
    func_name = frame.f_code.co_name
    if "self" in frame.f_locals:
        return f"{module_name}.{type(frame.f_locals['self']).__name__}.{func_name}"
    if "cls" in frame.f_locals and inspect.isclass(frame.f_locals["cls"]):
        return f"{module_name}.{frame.f_locals['cls'].__name__}.{func_name}"
    return f"{module_name}.{func_name}"


def _is_target_file(filename: str) -> bool:
    print("[DEBUG] enter _is_target_file | core:", {k: ((v[:100] + "...") if isinstance(v, str) and len(v) > 100 else v) for k, v in locals().items() if k in ("project_id", "doc_id", "file_path", "file_name", "file_type", "classification", "section_id", "run_id", "query", "page", "page_size", "status", "chunk_id")})
    abs_file = os.path.abspath(filename)
    if abs_file.endswith("method_trace.py"):
        return False
    return any(abs_file.startswith(target) for target in _TARGET_DIRS)


def _trace_calls(frame, event, arg):
    print("[DEBUG] enter _trace_calls | core:", {k: ((v[:100] + "...") if isinstance(v, str) and len(v) > 100 else v) for k, v in locals().items() if k in ("project_id", "doc_id", "file_path", "file_name", "file_type", "classification", "section_id", "run_id", "query", "page", "page_size", "status", "chunk_id")})
    if event != "call":
        return _trace_calls
    if not _is_target_file(frame.f_code.co_filename):
        return _trace_calls
    if getattr(_trace_state, "in_print", False):
        return _trace_calls

    _trace_state.in_print = True
    try:
        print(f"[MethodTrace] {_qualname(frame)} | vars: {_locals_snapshot(frame)}")
    finally:
        _trace_state.in_print = False
    return _trace_calls


def enable_method_trace() -> None:
    print("[DEBUG] enter enable_method_trace | core:", {k: ((v[:100] + "...") if isinstance(v, str) and len(v) > 100 else v) for k, v in locals().items() if k in ("project_id", "doc_id", "file_path", "file_name", "file_type", "classification", "section_id", "run_id", "query", "page", "page_size", "status", "chunk_id")})
    global _enabled
    if _enabled:
        return
    sys.settrace(_trace_calls)
    threading.settrace(_trace_calls)
    _enabled = True
    print(f"[MethodTrace] enabled | target_dirs={sorted(_TARGET_DIRS)}")

