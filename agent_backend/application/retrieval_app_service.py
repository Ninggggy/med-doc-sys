from __future__ import annotations

from typing import Any, Dict

from agent.agent_backend.services.knowledge_service import KnowledgeService


class RetrievalAppService:
    """Application-level retrieval orchestration for search and review context."""

    def __init__(self) -> None:
        self.knowledge_service = KnowledgeService()

    def semantic_search(self, query: str, filters: Dict[str, Any], top_k: int) -> Dict[str, Any]:
        """Run semantic search over the knowledge base."""
        return self.knowledge_service.semantic_query(
            query=query,
            classification=str(filters.get("classification", "") or ""),
            top_k=top_k,
            min_score=float(filters.get("min_score", 0.6) or 0.6),
        )

    def build_review_context(self, query: str, review_type: str, filters: Dict[str, Any]) -> Dict[str, Any]:
        """Build review-oriented retrieval context."""
        result = self.semantic_search(query=query, filters=filters, top_k=int(filters.get("top_k", 8) or 8))
        return {
            "query": query,
            "review_type": review_type,
            "filters": dict(filters),
            "retrieval_context": result,
        }

    def debug_retrieval(self, query: str, filters: Dict[str, Any]) -> Dict[str, Any]:
        """Return a verbose retrieval payload for debugging."""
        return self.build_review_context(query=query, review_type=str(filters.get("review_type", "general")), filters=filters)
