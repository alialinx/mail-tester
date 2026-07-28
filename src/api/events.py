import json
import time

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from src.config import SSE_TIMEOUT
from src.db.cache import event_channel, get_cache
from src.db.db import get_db

router = APIRouter()

FINAL_STATUSES = ("analyzed", "error", "expired")


def format_event(status: str) -> str:
    return "data: " + json.dumps({"status": status}) + "\n\n"


@router.get("/events/{to_address}", tags=["result"])
def stream_status(to_address: str, db=Depends(get_db)):
    def event_stream():
        pubsub = get_cache().pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe(event_channel(to_address))

        try:
            test_email = db.test_emails.find_one({"to_address": to_address}, {"status": 1})
            status = test_email.get("status") if test_email else "not found"

            yield format_event(status)

            if status in FINAL_STATUSES or status == "not found":
                return

            deadline = time.monotonic() + SSE_TIMEOUT

            while time.monotonic() < deadline:
                message = pubsub.get_message(timeout=15)

                if not message:
                    yield ": keepalive\n\n"
                    continue

                status = message.get("data")
                yield format_event(status)

                if status in FINAL_STATUSES:
                    return
        finally:
            pubsub.close()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
