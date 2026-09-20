"""Application service layer for orchestration-facing entry points."""

__all__ = [
    "KnowledgeAppService",
    "RetrievalAppService",
    "PreReviewAppService",
    "FeedbackAppService",
]


def __getattr__(name):
    if name == "KnowledgeAppService":
        from agent.agent_backend.application.knowledge_app_service import KnowledgeAppService

        return KnowledgeAppService
    if name == "RetrievalAppService":
        from agent.agent_backend.application.retrieval_app_service import RetrievalAppService

        return RetrievalAppService
    if name == "PreReviewAppService":
        from agent.agent_backend.application.pre_review_app_service import PreReviewAppService

        return PreReviewAppService
    if name == "FeedbackAppService":
        from agent.agent_backend.application.feedback_app_service import FeedbackAppService

        return FeedbackAppService
    raise AttributeError(name)
