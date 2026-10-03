"""Deterministic Jev backend and Karakeep API for tests."""

from __future__ import annotations

import json
from typing import Any

import httpx

from readworthy.config import Question
from readworthy.jev import Decision, NoulAnswer
from readworthy.karakeep import KarakeepClient


class FakeBackend:
    """Returns the answers given in `answers`; any other question gets noul 0. Records every call in `calls` so tests
    can inspect what was sent.
    """

    def __init__(self, answers: dict[str, NoulAnswer] | None = None):
        self.answers = answers or {}
        self.calls: list[tuple[Any, dict[str, Question]]] = []

    def decide(self, state: Any, questions: dict[str, Question]) -> Decision:
        self.calls.append((state, questions))
        return Decision({qid: self.answers.get(qid) or NoulAnswer(0.0) for qid in questions}, 0.0)


def link(content="Body text.", title="Title", archived=False, user_title=None):
    """A link bookmark as GET /bookmarks/{id} returns it, with `content` as its readable markdown."""
    return {
        "id": "b1",
        "title": user_title,
        "content": {"type": "link", "url": "https://example.com", "title": title},
        "archived": archived,
        "markdown": content,
    }


class FakeKarakeep:
    """A Karakeep API over httpx.MockTransport; records every request and applies bookmark updates.

    Bookmarks carry their readable content under "markdown", served by GET /bookmarks/{id}/content.
    """

    def __init__(self, *bookmarks, status=200):
        self.bookmarks = {b["id"]: b for b in bookmarks}
        self.status = status
        self.requests: list[tuple[str, str, object]] = []
        self.client = karakeep_with(self.handle, "kk-test")

    def handle(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, request.url.path, body))
        assert request.headers["authorization"] == "Bearer kk-test"
        if self.status != 200:
            return httpx.Response(self.status, json={"code": "x", "message": "nope"})
        _, _, _, _, bookmark_id, *rest = request.url.path.split("/")
        if bookmark_id not in self.bookmarks:
            return httpx.Response(404, json={"code": "NOT_FOUND", "message": "Bookmark not found"})
        bookmark = self.bookmarks[bookmark_id]
        if rest == ["content"]:
            assert request.url.params["format"] == "markdown"
            end = int(request.url.params["maxChars"])
            md = bookmark["markdown"]
            return httpx.Response(200, json={"content": md[:end]})
        if request.method == "GET":
            return httpx.Response(200, json={k: v for k, v in bookmark.items() if k != "markdown"})
        assert request.method == "PATCH" and rest == []
        bookmark.update(body)
        return httpx.Response(200, json={k: v for k, v in bookmark.items() if k != "markdown"})

    def writes(self):
        return [(m, p, body) for m, p, body in self.requests if m != "GET"]


def karakeep_with(handler, api_key="k"):
    return KarakeepClient("https://keep.example/", api_key, httpx.Client(transport=httpx.MockTransport(handler)))
