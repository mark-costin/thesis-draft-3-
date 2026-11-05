"""Calls to the CDSS API (the FastAPI backend in backend/).

These run in the Reflex backend process (Python), not in the browser: the browser never sees the
API address or the login token. The token is kept in a backend-only State variable (_token).

Settings come from the project's root .env, and only these two are read:
  CDSS_API_URL      where the API runs (default http://localhost:8000)
  GOOGLE_CLIENT_ID  the same OAuth client id the API checks Google tokens against
"""
import hashlib
import json as jsonlib
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from dotenv import dotenv_values

_ENV = dotenv_values(Path(__file__).resolve().parents[2] / ".env")  # repo root .env


def setting(name: str, default: str = "") -> str:
    return os.environ.get(name) or _ENV.get(name) or default


API_URL = setting("CDSS_API_URL", "http://localhost:8000").rstrip("/")
GOOGLE_CLIENT_ID = setting("GOOGLE_CLIENT_ID")
TIMEOUT = httpx.Timeout(20.0, connect=5.0)
_TRANSPORT = None  # tests set an httpx.ASGITransport here to call the API in-process


@dataclass
class Reply:
    status: int
    data: Any = None
    headers: dict[str, str] = field(default_factory=dict)
    content: bytes = b""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def message(self, fallback: str = "Something went wrong. Please try again.") -> str:
        """The API's own message for the patient. 422 lists are turned into one plain sentence."""
        if self.status == 0:
            return "Cannot reach the server. Check that the API is running, then try again."
        if self.status == 429:
            return "Too many tries. Please wait a minute and try again."
        detail = self.data.get("detail") if isinstance(self.data, dict) else None
        if isinstance(detail, str):
            return detail
        if isinstance(detail, dict) and isinstance(detail.get("message"), str):  # e.g. 409 with the existing id
            return detail["message"]
        if isinstance(detail, list) and detail:  # FastAPI validation errors
            first = detail[0]
            text = str(first.get("msg", fallback)).removeprefix("Value error, ")
            where = [str(p) for p in first.get("loc", []) if p not in ("body",)]
            return f"{text} ({' > '.join(where)})" if where else text
        return fallback


# Double submit: the same request again within this many seconds reuses its Idempotency-Key,
# so the API runs it once. Reflex handles one click after the other, so a fresh key per click
# would let a double click save two records.
REPEAT_WINDOW = 10.0
_recent: dict[str, tuple[str, float]] = {}  # request fingerprint -> (key, last time sent)


def _request_key(token: str | None, method: str, path: str, body: Any) -> tuple[str, str]:
    """(fingerprint, Idempotency-Key) for this request. The fingerprint holds only a hash."""
    raw = jsonlib.dumps([token, method, path, body], sort_keys=True, default=str)
    fingerprint = hashlib.sha256(raw.encode()).hexdigest()
    now = time.monotonic()
    for old, (_, sent) in list(_recent.items()):
        if now - sent > REPEAT_WINDOW:
            del _recent[old]
    key = _recent[fingerprint][0] if fingerprint in _recent else uuid.uuid4().hex
    _recent[fingerprint] = (key, now)
    return fingerprint, key


def _decode(response: httpx.Response) -> Any:
    if response.headers.get("content-type", "").startswith("application/json"):
        try:
            return response.json()
        except ValueError:
            return None
    return None


async def call(method: str, path: str, token: str | None = None, *, json: Any = None,
               params: dict | None = None, files: dict | None = None, idempotent: bool = False) -> Reply:
    """One API request. Never raises for HTTP or network errors: the caller reads Reply.status.

    idempotent=True sends an Idempotency-Key. The same request sent again within REPEAT_WINDOW
    seconds (a double click, or a retry after a lost connection) uses the same key, so the API
    saves one record. When the API answers a repeat with "Already saved" and a Location, the
    record is read from that Location, so the screen gets the same data as the first time."""
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    fingerprint = None
    if idempotent:
        fingerprint, headers["Idempotency-Key"] = _request_key(token, method, path, json)
    try:
        async with httpx.AsyncClient(base_url=API_URL, timeout=TIMEOUT, transport=_TRANSPORT) as client:
            response = await client.request(method, path, headers=headers, json=json, params=params, files=files)
            data = _decode(response)
            location = response.headers.get("location")
            if response.headers.get("idempotent-replayed") == "true" and location and response.status_code < 300:
                saved = await client.get(location, headers={k: v for k, v in headers.items() if k == "Authorization"})
                if saved.status_code == 200:
                    extra = data if isinstance(data, dict) else {}
                    # The replay's own fields (the PHQ-9 crisis prompt) win over the stored record's
                    data = (_decode(saved) or {}) | {k: v for k, v in extra.items() if k != "detail" and v is not None}
    except httpx.HTTPError:
        return Reply(0)  # the key is kept: trying again within the window cannot save twice
    if fingerprint and response.status_code >= 500:
        _recent.pop(fingerprint, None)  # the API keeps a 500 for the key; a retry needs a new one
    return Reply(response.status_code, data, dict(response.headers), response.content)
