"""Server-Sent Events wrapper for live demo step streaming."""
from __future__ import annotations

import json
import queue
import threading
import time
from collections.abc import Callable, Iterator

from fastapi.responses import StreamingResponse

StepFn = Callable[[Callable[[dict], None] | None], dict]

_HEARTBEAT_SEC = 12


def stream_demo_action(action: StepFn) -> StreamingResponse:
    """Run a demo action in a thread; stream each timeline step as SSE."""

    q: queue.Queue[tuple[str, object]] = queue.Queue()

    def on_step(step: dict) -> None:
        q.put(("step", step))

    def worker() -> None:
        try:
            result = action(on_step)
            q.put(("done", result))
        except Exception as exc:
            q.put(("error", str(exc)))

    threading.Thread(target=worker, daemon=True).start()

    def generate() -> Iterator[str]:
        while True:
            try:
                kind, payload = q.get(timeout=_HEARTBEAT_SEC)
            except queue.Empty:
                # Keep the connection alive through OCI LB / browser idle timeouts.
                yield _sse({"type": "ping", "ts": int(time.time())})
                continue
            if kind == "step":
                yield _sse({"type": "step", "step": payload})
            elif kind == "done":
                yield _sse({"type": "complete", "data": payload})
                break
            elif kind == "error":
                yield _sse({"type": "error", "error": payload})
                break

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"
