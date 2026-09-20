import json
import logging
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse
from starlette.responses import JSONResponse

from agent.agent_backend.llm.client import LLMClient, _is_verbose
from agent.agent_backend.llm.errors import LLMContextError
from agent.agent_backend.llm.model_setting import MODEL_CONFIG


LLM_DIR = Path(__file__).resolve().parent
LOG_DIR = LLM_DIR / "log"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    format="%(asctime)s - %(filename)s[line:%(lineno)d] - %(levelname)s: %(message)s",
    level=logging.INFO,
    filename=str(LOG_DIR / "llm_server.log"),
    filemode="a",
)

app = FastAPI()
client = LLMClient()


@app.exception_handler(LLMContextError)
async def context_error_handler(request, exc):
    return JSONResponse(status_code=422, content={'error': exc.as_dict(), 'message': str(exc)})


class ChatHistory(BaseModel):
    messages: List[dict] = Field(default=[{"role": "user", "content": "你好"}])


class ParseInput(BaseModel):
    messages: List[dict] = Field(default_factory=list)
    file: str = ""


class EmbeddingInput(BaseModel):
    texts: List[str] = Field(default_factory=list)
    dimensions: Optional[int] = None


class RerankInput(BaseModel):
    query: str = ""
    documents: List[str] = Field(default_factory=list)
    top_n: Optional[int] = None


@app.get("/health")
def health():
    return {
        "ok": True,
        "active": MODEL_CONFIG.get("active", {}),
        "chat_model_class": client._chat_model.__class__.__name__,
        "verbose": _is_verbose(),
    }


# Backward-compatible endpoint
@app.post("/fastchat")
async def chat_with_model(chat_history: ChatHistory):
    answer = client.chat(chat_history.messages, default="")
    logging.info("fastchat message_count=%s", len(chat_history.messages))
    return answer


@app.post("/chat")
async def chat(chat_history: ChatHistory):
    logging.info(
        "chat request | model_class=%s message_count=%s",
        client._chat_model.__class__.__name__,
        len(chat_history.messages or []),
    )
    answer = client.chat(chat_history.messages, default="")
    logging.info("chat response | output_chars=%s", len(str(answer or "")))
    return {"text": answer}


@app.post("/parse")
async def parse(body: ParseInput):
    if body.file:
        parsed = client.parse_layout(body.file)
        if parsed:
            return parsed
    answer = client.parse(messages=body.messages or [], file=body.file, default="")
    return {"text": answer}


@app.post("/embedding")
async def embedding(body: EmbeddingInput):
    vectors = [client.embed(t, dimensions=body.dimensions) for t in (body.texts or [])]
    return {"vectors": vectors}


@app.post("/rerank")
async def rerank(body: RerankInput):
    scores = client.rerank(query=body.query, documents=body.documents or [], top_n=body.top_n)
    return {"scores": scores}


async def _stream_text(text: str):
    step = 64
    for i in range(0, len(text), step):
        yield text[i : i + step]
    usage = {"chars": len(text)}
    yield f"<JSON_BEGIN>{json.dumps(usage, ensure_ascii=False)}<JSON_END>"


@app.post("/chat/stream")
async def chat_stream(chat_history: ChatHistory):
    text = client.chat(chat_history.messages, default="")
    return StreamingResponse(_stream_text(text), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("agent.agent_backend.llm.llm_server:app", host="127.0.0.1", port=8024, reload=True)
