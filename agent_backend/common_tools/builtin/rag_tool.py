from agent.agent_backend.memory.rag.pipeline import RAGPipeline


class RAGTool:
    def __init__(self):
        self.pipeline = RAGPipeline()

    def index_file(self, file_path: str, doc_id: str, classification: str = "", force: bool = False):
        return self.pipeline.index_file(file_path, doc_id, classification=classification, force=force)

    def ask(
        self,
        query: str,
        top_k: int = 5,
        alpha: float = 0.75,
        min_score: float = 0.6,
        filters: dict | None = None,
    ):
        return self.pipeline.build_context(
            query=query,
            top_k=top_k,
            alpha=alpha,
            min_score=min_score,
            filters=filters,
        )
