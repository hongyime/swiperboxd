"""Patch file to add QStash webhook support."""

from __future__ import annotations

import json
import threading
import time
from fastapi import HTTPException, Request

from .store import get_store
from .qstash_queue import QStashQueue


def _run_ingest_worker(user_id: str, source: str, depth_pages: int) -> None:
    """Background worker for ingest processing with error handling."""
    store = get_store()
    
    try:
        store.set_ingest_progress(user_id, 5)
        for value in [20, 35, 50, 70]:
            time.sleep(0.1)
            store.set_ingest_progress(user_id, value)

        # Import scraper here to avoid circular dependency
        from src.api import _execute_filter_pipeline
        _execute_filter_pipeline(user_id=user_id, source=source, depth_pages=depth_pages)
        
        store.set_ingest_progress(user_id, 100)
    except Exception as exc:
        store.set_ingest_progress(user_id, -1)
        raise exc
    finally:
        store.ingest_running.discard(user_id)


async def ingest_webhook(request: Request):
    """Webhook endpoint for QStash callbacks with signature verification."""
    body_bytes = await request.body()
    body = body_bytes.decode()

    try:
        queue_client = QStashQueue()
        is_valid = queue_client.verify_webhook(dict(request.headers), body)
        if not is_valid:
            raise HTTPException(status_code=401, detail={"code": "invalid_signature"})
    except ValueError:
        pass
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"code": "signature_verification_failed", "reason": str(exc)}) from exc

    try:
        payload = json.loads(body)
        user_id = payload.get("user_id")
        source = payload.get("source", "trending")
        depth_pages = payload.get("depth_pages", 2)

        if not user_id:
            raise HTTPException(status_code=400, detail={"code": "invalid_payload"})

        _run_ingest_worker(user_id, source, depth_pages)
        return {"status": "accepted", "user_id": user_id}

    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail={"code": "invalid_json"}) from exc
