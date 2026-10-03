"""Karakeep adapter: fetch a bookmark's content, classify it, archive the bookmark if it isn't for you.

Only `KarakeepClient` talks to the Karakeep API (shapes from its OpenAPI spec). Needs Karakeep v0.33.1 or later,
for `GET /bookmarks/{id}/content`.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from readworthy.core import MAX_INPUT_CHARS, Classifier, Result
from readworthy.errors import ClassifyError
from readworthy.httputil import error_detail

log = logging.getLogger("readworthy.karakeep")

DEFAULT_TIMEOUT = 30.0
WORKERS = 2

# Karakeep ids are cuid2 (lowercase letters and digits). Anything outside this set could change the request path.
_BOOKMARK_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
# Images in Karakeep's markdown (turndown: `![alt](src "title")`, brackets in alt unescaped). Some are inline
# data: URIs; none help classification.
_MD_IMAGE = re.compile(r'!\[(?:[^\[\]\n]|\[[^\]\n]*\])*\]\([^)\s]*(?:\s+"[^"\n]*")?\)')


def is_bookmark_id(value: object) -> bool:
    return isinstance(value, str) and _BOOKMARK_ID.fullmatch(value) is not None


class KarakeepError(ClassifyError):
    """A Karakeep request failed."""


class KarakeepClient:
    def __init__(self, base_url: str, api_key: str, client: httpx.Client | None = None):
        if not base_url:
            raise ClassifyError("KARAKEEP_URL is not set")
        if not api_key:
            raise ClassifyError("KARAKEEP_API_KEY is not set")
        self.base_url = _api_url(base_url)
        self.api_key = api_key
        self.client = client or httpx.Client(timeout=DEFAULT_TIMEOUT)

    @classmethod
    def from_env(cls) -> KarakeepClient:
        return cls(os.environ.get("KARAKEEP_URL", ""), os.environ.get("KARAKEEP_API_KEY", ""))

    def get_bookmark(self, bookmark_id: str) -> dict[str, Any]:
        data = self._request("GET", _path(bookmark_id))
        if not isinstance(data, dict):
            raise _bad("bookmark is not an object")
        return data

    def get_content(self, bookmark_id: str) -> str:
        """The bookmark's readable markdown (rendered by Karakeep), up to MAX_INPUT_CHARS, without images."""
        params = {"format": "markdown", "maxChars": MAX_INPUT_CHARS}
        data = self._request("GET", _path(bookmark_id, "/content"), params=params)
        if not isinstance(data, dict):
            raise _bad("bookmark content is not an object")
        content = data.get("content")
        if not isinstance(content, str):
            raise _bad("bookmark content has no string content")
        return _MD_IMAGE.sub("", content)

    def archive(self, bookmark_id: str) -> None:
        self._request("PATCH", _path(bookmark_id), json={"archived": True})

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            resp = self.client.request(method, self.base_url + path, headers=headers, **kwargs)
        # InvalidURL is not an HTTPError.
        except (httpx.HTTPError, httpx.InvalidURL) as e:
            raise KarakeepError(f"request to Karakeep failed: {e}") from None
        if not resp.is_success:
            detail = error_detail(resp, _error_message)
            raise KarakeepError(f"Karakeep returned HTTP {resp.status_code} for {method} {path}: {detail}")
        try:
            return resp.json()
        except ValueError:
            raise KarakeepError("Karakeep returned a non-JSON response") from None


def _api_url(base_url: str) -> str:
    try:
        url = httpx.URL(base_url)
    except httpx.InvalidURL as e:
        raise ClassifyError(f"KARAKEEP_URL is not a valid URL: {e}") from None
    if url.scheme not in ("http", "https") or not url.host or url.query or url.fragment:
        raise ClassifyError(f"KARAKEEP_URL must look like https://keep.example.com, got {base_url!r}")
    return base_url.rstrip("/") + "/api/v1"


def _path(bookmark_id: str, suffix: str = "") -> str:
    if not is_bookmark_id(bookmark_id):
        raise ClassifyError(f"invalid Karakeep bookmark id {str(bookmark_id)[:80]!r}")
    return f"/bookmarks/{bookmark_id}{suffix}"


def _error_message(body: Any) -> Any:
    return body.get("message") if isinstance(body, dict) else None


def _bad(why: str) -> KarakeepError:
    return KarakeepError(f"unexpected response from Karakeep: {why}")


def bookmark_title(bookmark: dict[str, Any]) -> str:
    """The title Karakeep shows: the one the user set, else the crawled page title."""
    content = bookmark.get("content")
    crawled = content.get("title") if isinstance(content, dict) else None
    for title in (bookmark.get("title"), crawled):
        if isinstance(title, str) and title.strip():
            return title.strip()
    return ""


def bookmark_text(bookmark: dict[str, Any], content: str) -> str:
    """The text to classify: the title, then the readable content."""
    body = re.sub(r"\n{3,}", "\n\n", content).strip()
    if not body:
        raise ClassifyError(f"bookmark {bookmark.get('id')} has no text to classify")
    title = bookmark_title(bookmark)
    return f"{title}\n\n{body}" if title else body


def classify_bookmark(classifier: Classifier, karakeep: KarakeepClient, bookmark_id: str) -> Result | None:
    """Classify a bookmark and archive it unless it is about a topic you like and not mainly one you dislike.

    An archived bookmark is left alone and None returned: there is nothing left to do, and classifying it would pay
    Jev for nothing. Anything else is classified on every event, so a re-crawl judges the bookmark again.
    """
    bookmark = karakeep.get_bookmark(bookmark_id)
    if bookmark.get("archived") is True:
        log.info("bookmark %s already archived", bookmark_id)
        return None
    result = classifier.classify(bookmark_text(bookmark, karakeep.get_content(bookmark_id)))
    if result.archive:
        karakeep.archive(bookmark_id)
    outcome = "archived" if result.archive else "kept"
    log.info("bookmark %s %s (wanted=%.2f) $%.6f", bookmark_id, outcome, result.wanted, result.cost_usd)
    return result


@dataclass(frozen=True)
class WebhookEvent:
    bookmark_id: str
    handled: bool  # a crawled link (including re-crawls) or a new text note


def parse_event(raw: bytes) -> WebhookEvent:
    """A Karakeep webhook body. ClassifyError if it isn't one."""
    try:
        body = json.loads(raw)
    except ValueError:
        raise ClassifyError("body is not JSON") from None
    if not isinstance(body, dict) or not is_bookmark_id(body.get("bookmarkId")):
        raise ClassifyError('body must be a JSON object with a Karakeep "bookmarkId"')
    handled = (body.get("operation"), body.get("type")) in {("crawled", "link"), ("created", "text")}
    return WebhookEvent(body["bookmarkId"], handled)


class ArchiveQueue:
    """Runs `run` (`classify_bookmark`) for webhook events on its own threads, so slow Jev calls never block requests.

    An event for a bookmark that is already waiting or running is merged into that run, so one bookmark is never
    classified twice at once (Karakeep retries deliveries and sends an event on every re-crawl). Failures can only
    be logged.
    """

    def __init__(self, run: Callable[[str], object], workers: int = WORKERS):
        self._run = run
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="readworthy")
        self._cond = threading.Condition()
        self._pending: set[str] = set()

    def submit(self, bookmark_id: str) -> Literal["queued", "merged"]:
        with self._cond:
            if bookmark_id in self._pending:
                return "merged"
            self._pending.add(bookmark_id)
        self._executor.submit(self._work, bookmark_id)
        return "queued"

    def join(self, timeout: float | None = None) -> bool:
        """Wait until no bookmark is waiting or running. False if `timeout` passed first."""
        with self._cond:
            return self._cond.wait_for(lambda: not self._pending, timeout)

    def close(self) -> None:
        """Drop the waiting runs and wait for the running ones to finish."""
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _work(self, bookmark_id: str) -> None:
        try:
            self._run(bookmark_id)
        except ClassifyError as e:
            log.error("bookmark %s not classified: %s", bookmark_id, e)
        except Exception:
            log.exception("bookmark %s not classified: unexpected error", bookmark_id)
        finally:
            with self._cond:
                self._pending.discard(bookmark_id)
                self._cond.notify_all()
