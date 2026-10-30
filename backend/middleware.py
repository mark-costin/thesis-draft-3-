"""HTTP middleware: CORS, rate limiting (SlowAPI) and idempotency keys.

Redis is optional during development. With REDIS_ENABLED=false (the default) the rate limit
counters and saved idempotent responses live in this process's memory, which is enough for one
developer running one API process. Set REDIS_ENABLED=true when Redis is running (Docker,
deployment) so several API processes share the same counters and keys.
"""

import base64
import hashlib
import json
import re
import time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from starlette.datastructures import Headers
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.config import settings

# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
# Per client IP. Routes that check passwords or send email get tighter limits in routes/auth.py.
DEFAULT_LIMIT = "120/minute"

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[DEFAULT_LIMIT],
    storage_uri=settings.redis_url if settings.redis_enabled else "memory://",
)

# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------
IDEMPOTENCY_HEADER = "idempotency-key"
REPLAY_HEADER = "Idempotent-Replayed"
_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_\-]{8,128}$")
# POST only: it is the method that creates records. PUT and PATCH set fixed values, so
# repeating them changes nothing, and skipping them keeps profile data out of the replay store.
_METHODS = {"POST"}
RESULT_TTL_SECONDS = 24 * 60 * 60   # a saved response is replayed for 24 hours
LOCK_TTL_SECONDS = 60               # how long a key stays "in progress" if the request dies


class MemoryIdempotencyStore:
    """One process only. Entries expire like Redis keys do."""

    def __init__(self):
        self._data: dict[str, tuple[float, dict]] = {}

    def _alive(self, key):
        item = self._data.get(key)
        if item is None:
            return None
        expires, value = item
        if expires < time.monotonic():
            del self._data[key]
            return None
        return value

    async def get(self, key):
        return self._alive(key)

    async def reserve(self, key, fingerprint) -> bool:
        if self._alive(key) is not None:
            return False
        self._data[key] = (time.monotonic() + LOCK_TTL_SECONDS, {"fingerprint": fingerprint})
        return True

    async def save(self, key, value):
        self._data[key] = (time.monotonic() + RESULT_TTL_SECONDS, value)

    async def release(self, key):
        self._data.pop(key, None)


class RedisIdempotencyStore:
    def __init__(self, url: str):
        from redis.asyncio import Redis

        self._redis = Redis.from_url(url)

    async def get(self, key):
        raw = await self._redis.get(key)
        return None if raw is None else json.loads(raw)

    async def reserve(self, key, fingerprint) -> bool:
        # SET NX: only the first request with this key gets to run
        return bool(await self._redis.set(key, json.dumps({"fingerprint": fingerprint}),
                                          nx=True, ex=LOCK_TTL_SECONDS))

    async def save(self, key, value):
        await self._redis.set(key, json.dumps(value), ex=RESULT_TTL_SECONDS)

    async def release(self, key):
        await self._redis.delete(key)


def _error(status: int, detail: str) -> Response:
    return JSONResponse({"detail": detail}, status_code=status)


class IdempotencyMiddleware:
    """Same Idempotency-Key twice = one action.

    The first POST with a key runs normally and its response is saved. A repeat with
    the same key and the same body gets the saved response back (header Idempotent-Replayed: true)
    without running again, so a double click or a retried request never creates two records.
    The same key with a different body is refused (422). Keys are scoped to the caller's token
    and the URL, so one patient's key can never replay another patient's response.
    Requests without the header are not affected.
    """

    def __init__(self, app: ASGIApp, store=None):
        self.app = app
        self.store = store or (RedisIdempotencyStore(settings.redis_url) if settings.redis_enabled
                               else MemoryIdempotencyStore())

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in _METHODS:
            return await self.app(scope, receive, send)
        headers = Headers(scope=scope)
        idem_key = headers.get(IDEMPOTENCY_HEADER)
        if idem_key is None:
            return await self.app(scope, receive, send)
        if not _KEY_PATTERN.fullmatch(idem_key):
            return await _error(400, "Idempotency-Key must be 8-128 letters, digits, - or _")(scope, receive, send)

        body = b""
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            body += message.get("body", b"")
            if not message.get("more_body", False):
                break

        fingerprint = hashlib.sha256(body).hexdigest()
        caller = hashlib.sha256(headers.get("authorization", "").encode()).hexdigest()
        store_key = "idem:" + hashlib.sha256(
            f"{caller}|{scope['method']}|{scope['path']}|{idem_key}".encode()).hexdigest()

        saved = await self.store.get(store_key)
        if saved is None and not await self.store.reserve(store_key, fingerprint):
            saved = await self.store.get(store_key)  # lost a race; read what the winner stored
        if saved is not None:
            if saved["fingerprint"] != fingerprint:
                return await _error(422, "This Idempotency-Key was already used with a different request")(
                    scope, receive, send)
            if "status" not in saved:
                return await _error(409, "A request with this Idempotency-Key is still being processed")(
                    scope, receive, send)
            replay = Response(content=base64.b64decode(saved["body"]), status_code=saved["status"],
                              media_type=saved.get("media_type"))
            replay.headers[REPLAY_HEADER] = "true"
            return await replay(scope, receive, send)

        body_sent = False

        async def replay_receive() -> Message:
            nonlocal body_sent
            if not body_sent:
                body_sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        response = {"status": 500, "media_type": None, "body": b""}

        async def capture_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                response["status"] = message["status"]
                response["media_type"] = Headers(raw=message["headers"]).get("content-type")
            elif message["type"] == "http.response.body":
                response["body"] += message.get("body", b"")
            await send(message)

        try:
            await self.app(scope, replay_receive, capture_send)
        except Exception:
            await self.store.release(store_key)
            raise

        if response["status"] >= 500:
            await self.store.release(store_key)  # a server error may be retried with the same key
        else:
            await self.store.save(store_key, {
                "fingerprint": fingerprint,
                "status": response["status"],
                "media_type": response["media_type"],
                "body": base64.b64encode(response["body"]).decode(),
            })


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------
def install(app: FastAPI) -> None:
    """Adds the middleware. Starlette runs the last one added first, so a request passes
    CORS -> rate limit -> idempotency -> route."""
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    app.add_middleware(IdempotencyMiddleware)
    app.add_middleware(SlowAPIMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,  # login uses a bearer token, not cookies
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
        expose_headers=[REPLAY_HEADER, "Retry-After"],
    )
