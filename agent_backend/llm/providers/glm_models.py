from __future__ import annotations

import json
import io
import os
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import request
from urllib.error import HTTPError, URLError

import requests

from agent.agent_backend.llm.base import EmbeddingModelBase, ParseModelBase, RerankModelBase


def _load_api_key(conf: Dict[str, Any]) -> str:
    p = Path(str(conf.get("api_key_path", "") or ""))
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    return ""


def _post_json(url: str, payload: Dict[str, Any], api_key: str, timeout: int) -> Dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    print(f"[LLM] GLM request payload_bytes={len(body)}")
    req = request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="ignore")
    print(f"[LLM] GLM response_chars={len(raw)}")
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


class GLMOCRParseModel(ParseModelBase):
    """GLM OCR parse model via /layout_parsing endpoint."""

    def __init__(self, conf: Dict[str, Any]):
        self.model = str(conf.get("model", "glm-ocr"))
        self.base_url = str(conf.get("base_url", "")).rstrip("/")
        self.timeout = int(conf.get("timeout", 60))
        self.api_key = _load_api_key(conf)
        self.poll_seconds = int(conf.get("poll_seconds", 2))
        self.max_polls = int(conf.get("max_polls", 60))

    def _parse_local_file(self, file_path: str) -> Dict[str, Any]:
        print("[LLM] GLMOCRParseModel local parse start")
        if not os.path.exists(file_path):
            return {}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        create_url = f"{self.base_url}/files/parser/create"
        suffix = Path(file_path).suffix.lower()
        file_type_map = {
            ".pdf": "PDF",
            ".doc": "DOC",
            ".docx": "DOC",
            ".png": "PNG",
            ".jpg": "JPG",
            ".jpeg": "JPG",
        }
        file_type = file_type_map.get(suffix, "DOC")
        with open(file_path, "rb") as fp:
            files = {"file": (Path(file_path).name, fp, "application/octet-stream")}
            data = {"tool_type": "lite", "file_type": file_type}
            create_resp = requests.post(create_url, headers=headers, data=data, files=files, timeout=self.timeout)
        create_resp.raise_for_status()
        create_json = create_resp.json()
        task_id = (
            create_json.get("task_id")
            or create_json.get("taskId")
            or create_json.get("id")
            or (create_json.get("data") or {}).get("task_id")
        )
        if not task_id:
            return {}
        result_url = f"{self.base_url}/files/parser/result/{task_id}/text"
        result_json: Dict[str, Any] = {}
        for _ in range(max(1, self.max_polls)):
            result_resp = requests.get(result_url, headers=headers, timeout=self.timeout)
            if result_resp.status_code == 200:
                try:
                    result_json = result_resp.json()
                except Exception:
                    result_json = {"text": result_resp.text}
                status = str(result_json.get("status", "") or "").lower()
                text = str(
                    result_json.get("md_results")
                    or result_json.get("text")
                    or result_json.get("result")
                    or result_json.get("content")
                    or ""
                ).strip()
                if status in {"succeeded", "success", "completed"} or text:
                    break
            time.sleep(max(1, self.poll_seconds))
        if isinstance(result_json, dict):
            text = str(
                result_json.get("md_results")
                or result_json.get("text")
                or result_json.get("result")
                or result_json.get("content")
                or ""
            ).strip()
            if not text:
                text = self._extract_text_from_result_package(result_json)
            result_json["md_results"] = text
        print(f"[LLM] GLMOCRParseModel output_chars={len(result_json.get('md_results', ''))}")
        return result_json

    def _decode_text_bytes(self, raw: bytes, name: str) -> str:
        print(
            f"[LLMDebug] GLMOCRParseModel._decode_text_bytes.input: "
            f"size={len(raw)}"
        )
        for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk", "utf-16", "utf-16le", "latin1"):
            try:
                text = raw.decode(encoding).strip()
            except Exception:
                continue
            if text:
                print(
                    f"[LLMDebug] GLMOCRParseModel._decode_text_bytes.output: "
                    f"encoding={encoding}, chars={len(text)}"
                )
                return text
        print(
            f"[LLMDebug] GLMOCRParseModel._decode_text_bytes.output: "
            f"encoding=none, chars=0"
        )
        return ""

    def _extract_text_from_json_bytes(self, raw: bytes, name: str) -> str:
        for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk", "utf-16", "utf-16le", "latin1"):
            try:
                data = json.loads(raw.decode(encoding, errors="ignore"))
            except Exception:
                continue
            if isinstance(data, dict):
                text = str(
                    data.get("md_results")
                    or data.get("text")
                    or data.get("content")
                    or data.get("result")
                    or ""
                ).strip()
                if text:
                    print(
                        f"[LLMDebug] GLMOCRParseModel._extract_text_from_json_bytes.output: "
                        f"encoding={encoding}, chars={len(text)}"
                    )
                    return text
        return ""

    def _extract_text_from_result_package(self, result_json: Dict[str, Any]) -> str:
        package_url = str(result_json.get("parsing_result_url", "") or "").strip()
        print(f"[LLM] GLMOCRParseModel has_result_package={bool(package_url)}")
        if not package_url:
            return ""
        try:
            resp = requests.get(package_url, timeout=self.timeout)
            resp.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
                names = archive.namelist()
                print(f"[LLM] GLMOCRParseModel package_members={len(names)}")
                preferred = [name for name in names if name.lower().endswith((".md", ".txt", ".json"))]
                for name in preferred:
                    try:
                        raw = archive.read(name)
                    except Exception:
                        continue
                    print(
                        f"[LLMDebug] GLMOCRParseModel._extract_text_from_result_package.member: "
                        f"size={len(raw)}"
                    )
                    if name.lower().endswith(".json"):
                        text = self._extract_text_from_json_bytes(raw, name)
                        if text:
                            return text
                    else:
                        text = self._decode_text_bytes(raw, name)
                        if text:
                            return text
        except Exception as exc:
            print(f"[LLM] GLMOCRParseModel package error_type={type(exc).__name__}")
        return ""

    def parse_layout(self, file: str) -> Dict[str, Any]:
        print(f"[LLM] GLMOCRParseModel parse_layout has_file={bool(file)}")
        if not self.api_key or not self.base_url or not file:
            return {}
        if os.path.exists(file):
            try:
                return self._parse_local_file(file)
            except Exception as exc:
                print(f"[LLM] GLMOCRParseModel local error_type={type(exc).__name__}")
                return {}
        try:
            result = _post_json(
                url=f"{self.base_url}/layout_parsing",
                payload={"model": self.model, "file": file},
                api_key=self.api_key,
                timeout=self.timeout,
            )
            print(f"[LLM] GLMOCRParseModel layout_result_fields={len(result)}")
            return result
        except (HTTPError, URLError, TimeoutError, ValueError, TypeError, json.JSONDecodeError):
            return {}
        except Exception as exc:
            print(f"[LLM] GLMOCRParseModel remote error_type={type(exc).__name__}")
            return {}

    def parse(
        self,
        messages: Optional[List[Dict[str, str]]] = None,
        file: str = "",
        default: str = "",
    ) -> str:
        print(f"[LLM] GLMOCRParseModel parse has_file={bool(file)}")
        file_ref = (file or "").strip()
        if not file_ref and messages:
            user_text = "\n".join(str(m.get("content", "")) for m in messages if m.get("role") == "user")
            user_text = user_text.strip()
            if user_text.startswith("http://") or user_text.startswith("https://"):
                file_ref = user_text
        if not file_ref:
            return default

        data = self.parse_layout(file_ref)
        if not data:
            return default
        md = str(data.get("md_results", "") or "").strip()
        print(f"[LLM] GLMOCRParseModel output_chars={len(md or default)}")
        return md or default


class GLMEmbeddingModel(EmbeddingModelBase):
    """GLM embedding model via /embeddings endpoint."""

    def __init__(self, conf: Dict[str, Any]):
        self.model = str(conf.get("model", "embedding-3"))
        self.base_url = str(conf.get("base_url", "")).rstrip("/")
        self.timeout = int(conf.get("timeout", 30))
        self.api_key = _load_api_key(conf)
        self.default_dimensions = conf.get("dimensions", None)

    def embed(self, text: str, dimensions: Optional[int] = None) -> List[float]:
        print(f"[LLMDebug] GLMEmbeddingModel.embed.input.dimensions: {dimensions}")
        print(f"[LLM] GLMEmbeddingModel input_chars={len(text or '')}")
        if not self.api_key or not self.base_url:
            return []
        payload: Dict[str, Any] = {
            "model": self.model,
            "input": text,
        }
        dim = dimensions if dimensions is not None else self.default_dimensions
        if dim is not None:
            try:
                payload["dimensions"] = int(dim)
            except Exception:
                pass
        try:
            data = _post_json(
                url=f"{self.base_url}/embeddings",
                payload=payload,
                api_key=self.api_key,
                timeout=self.timeout,
            )
            arr = data.get("data") if isinstance(data, dict) else None
            if isinstance(arr, list) and arr:
                emb = arr[0].get("embedding", [])
                if isinstance(emb, list):
                    result = [float(x) for x in emb]
                    print(f"[LLMDebug] GLMEmbeddingModel.embed.output.dim: {len(result)}")
                    return result
        except (HTTPError, URLError, TimeoutError, ValueError, TypeError, json.JSONDecodeError):
            return []
        except Exception:
            return []
        return []


class GLMRerankModel(RerankModelBase):
    """GLM rerank model via /rerank endpoint."""

    def __init__(self, conf: Dict[str, Any]):
        self.model = str(conf.get("model", "rerank"))
        self.base_url = str(conf.get("base_url", "")).rstrip("/")
        self.timeout = int(conf.get("timeout", 30))
        self.api_key = _load_api_key(conf)

    def rerank(self, query: str, documents: List[str], top_n: Optional[int] = None) -> List[float]:
        print(f"[LLM] GLMRerankModel query_chars={len(query or '')}")
        print(f"[LLMDebug] GLMRerankModel.rerank.input.top_n: {top_n}")
        print(f"[LLM] GLMRerankModel document_count={len(documents or [])}")
        if not documents:
            return []
        if not self.api_key or not self.base_url:
            return [0.0 for _ in documents]

        payload: Dict[str, Any] = {
            "model": self.model,
            "query": query,
            "documents": documents,
        }
        if top_n is not None:
            try:
                payload["top_n"] = int(top_n)
            except Exception:
                pass

        try:
            data = _post_json(
                url=f"{self.base_url}/rerank",
                payload=payload,
                api_key=self.api_key,
                timeout=self.timeout,
            )
        except (HTTPError, URLError, TimeoutError, ValueError, TypeError, json.JSONDecodeError):
            return [0.0 for _ in documents]
        except Exception:
            return [0.0 for _ in documents]

        scores = [0.0 for _ in documents]
        results = data.get("results") if isinstance(data, dict) else None
        if isinstance(results, list):
            for item in results:
                try:
                    idx = int(item.get("index"))
                    score = float(item.get("relevance_score", 0.0))
                except Exception:
                    continue
                if 0 <= idx < len(scores):
                    scores[idx] = score
        print("[LLMDebug] GLMRerankModel.rerank.output.scores:")
        print(json.dumps(scores, ensure_ascii=False))
        return scores
