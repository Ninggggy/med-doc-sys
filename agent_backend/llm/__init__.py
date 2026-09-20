__all__ = ["LLMClient", "ModelFactory"]


def __getattr__(name):
    if name == "LLMClient":
        from agent.agent_backend.llm.client import LLMClient

        return LLMClient
    if name == "ModelFactory":
        from agent.agent_backend.llm.factory import ModelFactory

        return ModelFactory
    raise AttributeError(name)
