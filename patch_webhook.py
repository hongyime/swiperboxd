"""Patch script to add webhook support to app.py"""

# Read current app.py
with open('src/api/app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Add webhook code
webhook_code = '''async def ingest_webhook(request: Request):
    """Webhook endpoint for QStash callbacks when ingest jobs complete."""
    body_bytes = await request.body()
    body = body_bytes.decode()

    try:
        from .qstash_queue import QStashQueue
        queue_client = QStashQueue()
        is_valid = queue_client.verify_webhook(dict(request.headers), body)
        if not is_valid:
            raise HTTPException(status_code=401, detail={"code": "invalid_signature"})
    except ValueError:
        pass  # QStash not configured
    except Exception as exc:
        raise HTTPException(status_code=500, detail={"code": "signature_verification_failed", "reason": str(exc)}) from exc

    try:
        payload = json.loads(body)
        user_id = payload.get("user_id")
        source = payload.get("source", "trending")
        depth_pages = payload.get("depth_pages", 2)
        if not user_id:
            raise HTTPException(status_code=400, detail={"code": "invalid_payload"})
        
        # Call existing _run_ingest_worker (need to rename _simulate_ingest first)
        _simulate_ingest(user_id, source, depth_pages)
        return {"status": "accepted", "user_id": user_id}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail={"code": "invalid_json"}) from exc


@app.post("/webhooks/ingest")
async def handle_ingest_webhook(request: Request):
    return await ingest_webhook(request)


'''

# Insert after line with "store.ingest_running.discard"
marker = "store.ingest_running.discard(user_id)"
if marker in content:
    pos = content.find(marker) + len(marker)
    content = content[:pos] + "\n" + webhook_code + "\n" + content[pos:]
    
    with open('src/api/app.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print("Webhook added successfully")
else:
    print("Marker not found")
