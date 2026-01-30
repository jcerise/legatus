"""Minimal Mem0 REST API server wrapping the mem0ai library."""

import logging
import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

app = FastAPI(title="Mem0 Server")


class Message(BaseModel):
    role: str
    content: str


class MemoryCreate(BaseModel):
    messages: list[Message]
    user_id: str | None = None
    agent_id: str | None = None
    run_id: str | None = None
    metadata: dict | None = None


class SearchRequest(BaseModel):
    query: str
    user_id: str | None = None
    agent_id: str | None = None
    run_id: str | None = None
    limit: int = 10


def _get_memory():
    """Lazy-init the Memory instance."""
    if not hasattr(app.state, "memory"):
        from mem0 import Memory

        config = {
            "vector_store": {
                "provider": "redis",
                "config": {
                    "collection_name": os.getenv(
                        "MEM0_COLLECTION", "legatus_memories"
                    ),
                    "embedding_model_dims": 1536,
                    "redis_url": os.getenv("MEM0_REDIS_URL", "redis://redis:6379"),
                },
            },
            "version": "v1.1",
        }
        app.state.memory = Memory.from_config(config)
    return app.state.memory


def _filter_none(**kwargs) -> dict:
    return {k: v for k, v in kwargs.items() if v is not None}


@app.get("/")
def root():
    return {"message": "Mem0 server is running"}


@app.post("/memories")
def add_memory(req: MemoryCreate):
    try:
        m = _get_memory()
        messages = [msg.model_dump() for msg in req.messages]
        params = _filter_none(
            user_id=req.user_id,
            agent_id=req.agent_id,
            run_id=req.run_id,
            metadata=req.metadata,
        )
        result = m.add(messages=messages, **params)
        return result
    except Exception as e:
        logger.exception("Failed to add memory")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/memories")
def get_memories(
    user_id: str | None = None,
    agent_id: str | None = None,
    run_id: str | None = None,
):
    try:
        m = _get_memory()
        params = _filter_none(user_id=user_id, agent_id=agent_id, run_id=run_id)
        if not params:
            raise HTTPException(
                status_code=400,
                detail="At least one of user_id, agent_id, or run_id is required",
            )
        result = m.get_all(**params)
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to get memories")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.get("/memories/{memory_id}")
def get_memory(memory_id: str):
    try:
        m = _get_memory()
        result = m.get(memory_id)
        return result
    except Exception as e:
        logger.exception("Failed to get memory")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.post("/search")
def search_memories(req: SearchRequest):
    try:
        m = _get_memory()
        params = _filter_none(
            user_id=req.user_id,
            agent_id=req.agent_id,
            run_id=req.run_id,
            limit=req.limit,
        )
        result = m.search(query=req.query, **params)
        return result
    except Exception as e:
        logger.exception("Failed to search memories")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.delete("/memories/{memory_id}")
def delete_memory(memory_id: str):
    try:
        m = _get_memory()
        m.delete(memory_id)
        return {"message": "Memory deleted"}
    except Exception as e:
        logger.exception("Failed to delete memory")
        raise HTTPException(status_code=500, detail=str(e)) from e


@app.delete("/memories")
def delete_all_memories(
    user_id: str | None = None,
    agent_id: str | None = None,
    run_id: str | None = None,
):
    try:
        m = _get_memory()
        params = _filter_none(user_id=user_id, agent_id=agent_id, run_id=run_id)
        if not params:
            raise HTTPException(
                status_code=400,
                detail="At least one of user_id, agent_id, or run_id is required",
            )
        m.delete_all(**params)
        return {"message": "Memories deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to delete memories")
        raise HTTPException(status_code=500, detail=str(e)) from e
