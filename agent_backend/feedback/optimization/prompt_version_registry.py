from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from agent.agent_backend.config.settings import settings
from agent.agent_backend.utils.file_util import ensure_dir_exists


class PromptVersionRegistry:
    """File-based prompt version registry with candidate/active/rollback states."""

    def __init__(self) -> None:
        self.base_dir = Path(settings.feedback_asset_dir) / "prompt_registry"
        self.index_path = self.base_dir / "index.json"
        ensure_dir_exists(str(self.base_dir))
        if not self.index_path.exists():
            self._write_index({"active_version_id": "", "versions": []})

    def _read_index(self) -> Dict[str, Any]:
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except Exception:
            return {"active_version_id": "", "versions": []}

    def _write_index(self, payload: Dict[str, Any]) -> None:
        self.index_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def register_candidate(
        self,
        version_id: str,
        bundle_path: str,
        source_feedback: Dict[str, Any],
        diagnosis: Dict[str, Any],
        replay_evaluation: Dict[str, Any],
        target_templates: List[str],
    ) -> Dict[str, Any]:
        index = self._read_index()
        versions = index.get("versions", []) if isinstance(index.get("versions", []), list) else []
        record = {
            "version_id": str(version_id or "").strip(),
            "bundle_path": str(bundle_path or "").strip(),
            "status": "candidate",
            "created_at": datetime.now().isoformat(),
            "activated_at": "",
            "rolled_back_at": "",
            "source_feedback": {
                "run_id": str(source_feedback.get("run_id", "") or ""),
                "section_id": str(source_feedback.get("section_id", "") or ""),
                "decision": str(source_feedback.get("decision", "") or ""),
                "labels": list(source_feedback.get("labels", []) or []),
            },
            "diagnosis": diagnosis if isinstance(diagnosis, dict) else {},
            "target_templates": [str(x).strip() for x in target_templates if str(x).strip()],
            "replay_evaluation": replay_evaluation if isinstance(replay_evaluation, dict) else {},
            "auto_recommendation": "manual_review_required",
        }
        versions = [item for item in versions if str((item or {}).get("version_id", "")) != record["version_id"]]
        versions.append(record)
        index["versions"] = versions
        self._write_index(index)
        return record

    def list_versions(self) -> Dict[str, Any]:
        index = self._read_index()
        versions = index.get("versions", []) if isinstance(index.get("versions", []), list) else []
        versions.sort(key=lambda item: str((item or {}).get("created_at", "")), reverse=True)
        return {
            "active_version_id": str(index.get("active_version_id", "") or ""),
            "versions": versions,
        }

    def get_active_version(self) -> Dict[str, Any]:
        index = self._read_index()
        active_version_id = str(index.get("active_version_id", "") or "")
        for item in index.get("versions", []) if isinstance(index.get("versions", []), list) else []:
            if str((item or {}).get("version_id", "") or "") == active_version_id:
                return item if isinstance(item, dict) else {}
        return {}

    def activate_version(self, version_id: str) -> Dict[str, Any]:
        target = str(version_id or "").strip()
        index = self._read_index()
        versions = index.get("versions", []) if isinstance(index.get("versions", []), list) else []
        found = None
        for item in versions:
            if not isinstance(item, dict):
                continue
            vid = str(item.get("version_id", "") or "")
            if vid == target:
                item["status"] = "active"
                item["activated_at"] = datetime.now().isoformat()
                found = item
            elif item.get("status") == "active":
                item["status"] = "archived"
        if found is None:
            raise FileNotFoundError(f"prompt version not found: {target}")
        index["active_version_id"] = target
        index["versions"] = versions
        self._write_index(index)
        return found

    def rollback_active(self) -> Dict[str, Any]:
        index = self._read_index()
        active_version_id = str(index.get("active_version_id", "") or "")
        versions = index.get("versions", []) if isinstance(index.get("versions", []), list) else []
        rolled_back = None
        for item in versions:
            if not isinstance(item, dict):
                continue
            if str(item.get("version_id", "") or "") == active_version_id:
                item["status"] = "rolled_back"
                item["rolled_back_at"] = datetime.now().isoformat()
                rolled_back = item
                break
        index["active_version_id"] = ""
        index["versions"] = versions
        self._write_index(index)
        return rolled_back or {}

    def resolve_active_prompt_config(self) -> Dict[str, Any]:
        active = self.get_active_version()
        bundle_path = str(active.get("bundle_path", "") or "").strip()
        version_id = str(active.get("version_id", "") or "").strip()
        if not bundle_path or not version_id:
            return {}
        return {
            "prompt_version_id": version_id,
            "prompt_bundle_path": bundle_path,
        }
