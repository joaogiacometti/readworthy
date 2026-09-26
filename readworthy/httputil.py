"""Helpers shared by the HTTP clients (Jev, Karakeep)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

MAX_DETAIL_CHARS = 300


def error_detail(resp: httpx.Response, message: Callable[[Any], Any]) -> str:
    """A short description of an error response: the API's own message (`message(json_body)`), else the body."""
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:MAX_DETAIL_CHARS] or resp.reason_phrase
    return str(message(body) or body)[:MAX_DETAIL_CHARS]
