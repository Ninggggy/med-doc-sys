from __future__ import annotations

from typing import Any, Dict, List

from agent.agent_backend.services.file_service import FileService
from agent.agent_backend.services.knowledge_service import KnowledgeService


class KnowledgeAppService:
    """Application-level orchestration for knowledge upload, parse, and query."""

    def __init__(self) -> None:
        self.file_service = FileService()
        self.knowledge_service = KnowledgeService()

    def upload_knowledge(self, files: List[Any], meta: Dict[str, Any]) -> Dict[str, Any]:
        """Upload one or more knowledge files."""
        results = []
        for file_obj in files or []:
            success, message, data = self.file_service.upload_file(
                file_obj=file_obj,
                classification=str(meta.get("classification", "other")),
                affect_range=str(meta.get("affect_range", "other")),
            )
            results.append({"success": success, "message": message, "data": data})
        return {"results": results, "count": len(results)}

    def submit_parse(self, doc_id: str) -> Dict[str, Any]:
        """Submit a parse request for an uploaded knowledge file."""
        success, message, data = self.knowledge_service.parse_document(doc_id=doc_id, force=True)
        return {"success": success, "message": message, "data": data}

    def query_knowledge(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        """Run semantic or filtered knowledge query."""
        query = str(filters.get("query", "") or "")
        if query:
            return self.knowledge_service.query_knowledge(
                query=query,
                file_name=str(filters.get("file_name", "") or ""),
                classification=str(filters.get("classification", "") or ""),
                page=int(filters.get("page", 1) or 1),
                page_size=int(filters.get("page_size", 10) or 10),
            )
        return self.file_service.page_query_files(
            file_name=str(filters.get("file_name", "") or ""),
            doc_id=str(filters.get("doc_id", "") or ""),
            classification=str(filters.get("classification", "") or ""),
            page=int(filters.get("page", 1) or 1),
            page_size=int(filters.get("page_size", 10) or 10),
        )

    def delete_knowledge(self, doc_id: str) -> Dict[str, Any]:
        """Delete a knowledge file and its indexed chunks."""
        success, message = self.file_service.delete_file(doc_id=doc_id)
        return {"success": success, "message": message}

    def rebuild_index(self, doc_id: str) -> Dict[str, Any]:
        """Rebuild document parsing and retrieval index."""
        return self.submit_parse(doc_id)

    def get_parse_progress(self, task_id: str) -> Dict[str, Any]:
        """Return parse progress snapshot for a task/document id."""
        return self.knowledge_service.get_parse_progress(task_id=task_id)
