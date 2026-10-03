"""Client for TypeSafe Jev through OpenRouter's Decisions API: the only code that talks to it.

Request and response shapes were checked against a real call; see tests/fixtures/jev/decision_response.json.
"""

from __future__ import annotations

import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from readworthy.config import Question
from readworthy.errors import ClassifyError, is_number
from readworthy.httputil import error_detail

API_URL = "https://openrouter.ai/api/alpha/decisions"
DEFAULT_MODEL = "~typesafe/jev-latest"
DEFAULT_TIMEOUT = 60.0
# The API asks clients to back off and retry on these.
RETRY_STATUSES = {429, 529}
MAX_ATTEMPTS = 4


@dataclass(frozen=True)
class NoulAnswer:
    noul: float  # probability that the answer is yes


@dataclass(frozen=True)
class Decision:
    answers: dict[str, NoulAnswer]
    cost_usd: float


class JevBackend:
    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not api_key:
            raise ClassifyError("OPENROUTER_API_KEY is not set")
        self.api_key = api_key
        self.model = model
        self.client = client or httpx.Client(timeout=DEFAULT_TIMEOUT)
        self.sleep = sleep

    @classmethod
    def from_env(cls) -> JevBackend:
        return cls(
            api_key=os.environ.get("OPENROUTER_API_KEY", ""),
            model=os.environ.get("READWORTHY_MODEL") or DEFAULT_MODEL,
        )

    def decide(self, state: Any, questions: dict[str, Question]) -> Decision:
        """Answer every question about `state`, in one request."""
        body = {
            "model": self.model,
            "state": state,
            "questions": {qid: question_body(q) for qid, q in questions.items()},
        }
        return parse_response(self._post(body), questions)

    def _post(self, body: dict[str, Any]) -> Any:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        for attempt in range(MAX_ATTEMPTS):
            try:
                resp = self.client.post(API_URL, json=body, headers=headers)
            except httpx.HTTPError as e:
                raise ClassifyError(f"request to OpenRouter failed: {e}") from None
            if resp.status_code not in RETRY_STATUSES or attempt == MAX_ATTEMPTS - 1:
                break
            self.sleep(_retry_delay(resp, attempt))
        if resp.status_code != 200:
            raise ClassifyError(f"OpenRouter returned HTTP {resp.status_code}: {error_detail(resp, _error_message)}")
        try:
            return resp.json()
        except ValueError:
            raise ClassifyError("OpenRouter returned a non-JSON response") from None


def question_body(q: Question) -> dict[str, Any]:
    """A question as the Decisions API takes it."""
    return {"type": "noul", "instructions": q.instructions, "criteria": q.criteria}


def parse_response(data: Any, questions: dict[str, Question]) -> Decision:
    """Validate a Decisions API response against the questions asked."""
    if not isinstance(data, dict):
        raise _bad("body is not an object")
    if "error" in data:
        raise ClassifyError(f"Jev returned an error: {data['error']}")
    raw_answers = data.get("answers")
    if not isinstance(raw_answers, dict):
        raise _bad("missing answers")
    answers = {}
    for qid in questions:
        a = raw_answers.get(qid)
        if not isinstance(a, dict):
            raise _bad(f"no answer for question {qid!r}")
        if a.get("type") != "noul":
            raise _bad(f"answer {qid!r} has type {a.get('type')!r}, expected 'noul'")
        answers[qid] = NoulAnswer(_probability(a.get("noul"), f"{qid}.noul"))

    usage = data.get("usage")
    if not isinstance(usage, dict):
        raise _bad("missing usage")
    return Decision(answers, _number(usage.get("cost"), "usage.cost"))


def _bad(why: str) -> ClassifyError:
    return ClassifyError(f"unexpected response from Jev: {why}")


def _number(value: Any, field: str) -> float:
    if not is_number(value) or not math.isfinite(value):
        raise _bad(f"{field} is not a number")
    return float(value)


def _probability(value: Any, field: str) -> float:
    p = _number(value, field)
    if not 0.0 <= p <= 1.0:
        raise _bad(f"{field} must be between 0 and 1")
    return p


def _retry_delay(resp: httpx.Response, attempt: int) -> float:
    try:
        delay = float(resp.headers["retry-after"])
    except (KeyError, ValueError):
        delay = math.nan
    if not math.isfinite(delay) or delay < 0:
        return 0.5 * 2**attempt
    return min(delay, 30.0)


def _error_message(body: Any) -> Any:
    err = body.get("error") if isinstance(body, dict) else None
    return err.get("message") if isinstance(err, dict) and err.get("message") else err
