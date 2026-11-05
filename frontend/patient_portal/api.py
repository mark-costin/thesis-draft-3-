"""Calls to the CDSS API (the FastAPI backend in backend/).

These run in the Reflex backend process (Python), not in the browser: the browser never sees the
API address or the login token. The token is kept in a backend-only State variable (_token).

Settings come from the project's root .env, and only these two are read:
  CDSS_API_URL      where the API runs (default http://localhost:8000)
  GOOGLE_CLIENT_ID  the same OAuth client id the API checks Google tokens against
"""
import os
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
        if isinstance(detail, list) and detail:  # FastAPI validation errors
            first = detail[0]
            text = str(first.get("msg", fallback)).removeprefix("Value error, ")
            where = [str(p) for p in first.get("loc", []) if p not in ("body",)]
            return f"{text} ({' > '.join(where)})" if where else text
        return fallback


async def call(method: str, path: str, token: str | None = None, *, json: Any = None,
               params: dict | None = None, files: dict | None = None, idempotent: bool = False) -> Reply:
    """One API request. Never raises for HTTP or network errors: the caller reads Reply.status.
    idempotent=True adds a fresh Idempotency-Key, so a double click saves one record."""
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if idempotent:
        headers["Idempotency-Key"] = uuid.uuid4().hex
    try:
        async with httpx.AsyncClient(base_url=API_URL, timeout=TIMEOUT, transport=_TRANSPORT) as client:
            response = await client.request(method, path, headers=headers, json=json, params=params, files=files)
    except httpx.HTTPError:
        return Reply(0)
    data = None
    if response.headers.get("content-type", "").startswith("application/json"):
        try:
            data = response.json()
        except ValueError:
            data = None
    return Reply(response.status_code, data, dict(response.headers), response.content)
