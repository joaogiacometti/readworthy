import copy
import json

import httpx
import pytest
from helpers import backend_replaying, backend_with

from readworthy.config import Question
from readworthy.errors import ClassifyError
from readworthy.jev import DEFAULT_MODEL, ChoiceAnswer, JevBackend, NoulAnswer, parse_response, question_body


def test_parse_response(config, recorded):
    d = parse_response(recorded, config.questions)
    assert d.cost_usd == pytest.approx(4.9014e-05)
    kind = d.answers["kind"]
    assert isinstance(kind, ChoiceAnswer) and kind.probabilities["explanation"] == 1
    assert list(kind.probabilities) == config.questions["kind"].options
    assert d.answers["teaches"] == NoulAnswer(0.98)


def test_decide_over_http(config, recorded):
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=recorded)

    d = backend_with(handler).decide({"content": "hello"}, config.questions)
    assert d.answers["kind"].probabilities["explanation"] == 1
    assert seen["auth"] == "Bearer sk-test"
    assert seen["url"] == "https://openrouter.ai/api/alpha/decisions"
    assert seen["body"]["model"] == DEFAULT_MODEL
    assert seen["body"]["state"] == {"content": "hello"}
    assert seen["body"]["questions"] == {qid: question_body(q) for qid, q in config.questions.items()}


def test_question_body(config):
    teaches = config.questions["teaches"]
    expected = {"type": "noul", "instructions": teaches.instructions, "criteria": teaches.criteria}
    assert question_body(teaches) == expected
    assert "criteria" not in question_body(Question("hype", "noul", "x"))


def test_missing_key():
    with pytest.raises(ClassifyError, match="OPENROUTER_API_KEY"):
        JevBackend.from_env()


def test_model_from_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    assert JevBackend.from_env().model == DEFAULT_MODEL
    monkeypatch.setenv("READWORTHY_MODEL", "typesafe/jev-1.13")
    assert JevBackend.from_env().model == "typesafe/jev-1.13"


def test_retries_rate_limits(config, recorded):
    sleeps = []
    backend_replaying(
        httpx.Response(429, headers={"retry-after": "2"}),
        httpx.Response(529),
        httpx.Response(200, json=recorded),
        sleeps=sleeps,
    ).decide({"content": "x"}, config.questions)
    assert sleeps == [2.0, 1.0]


@pytest.mark.parametrize("header", ["-1", "nan", "inf", "soon"])
def test_bad_retry_after_uses_backoff(config, recorded, header):
    sleeps = []
    backend_replaying(
        httpx.Response(429, headers={"retry-after": header}),
        httpx.Response(200, json=recorded),
        sleeps=sleeps,
    ).decide({"content": "x"}, config.questions)
    assert sleeps == [0.5]


def test_gives_up_after_retries(config):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(429, json={"error": {"message": "Rate limit exceeded"}})

    with pytest.raises(ClassifyError, match="HTTP 429: Rate limit exceeded"):
        backend_with(handler).decide({"content": "x"}, config.questions)
    assert len(calls) == 4


def test_http_error_not_retried(config):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(401, json={"error": {"code": 401, "message": "No auth credentials found"}})

    with pytest.raises(ClassifyError, match="HTTP 401: No auth credentials found"):
        backend_with(handler).decide({"content": "x"}, config.questions)
    assert len(calls) == 1


def test_network_error(config):
    def handler(request):
        raise httpx.ConnectError("boom")

    with pytest.raises(ClassifyError, match="request to OpenRouter failed"):
        backend_with(handler).decide({"content": "x"}, config.questions)


def test_non_json(config):
    with pytest.raises(ClassifyError, match="non-JSON"):
        backend_with(lambda r: httpx.Response(200, text="<html>")).decide({"content": "x"}, config.questions)


@pytest.mark.parametrize(
    "mutation, msg",
    [
        (lambda d: d.pop("answers"), "missing answers"),
        (lambda d: d["answers"].pop("hype"), "no answer for question 'hype'"),
        (lambda d: d["answers"]["hype"].update(type="choice"), "expected 'noul'"),
        (lambda d: d["answers"]["hype"].update(noul=1.2), "between 0 and 1"),
        (lambda d: d["answers"]["hype"].pop("noul"), "hype.noul is not a number"),
        (lambda d: d["answers"]["kind"]["probabilities"].pop("other"), "do not match"),
        (lambda d: d["answers"]["kind"]["probabilities"].update(other="high"), "not a number"),
        (lambda d: d["answers"]["kind"].update(probabilities=5), "keys 5 do not match"),
        (lambda d: d.pop("usage"), "usage"),
        (lambda d: d["usage"].pop("cost"), "usage.cost"),
        (lambda d: d.update(error={"message": "bad"}), "Jev returned an error"),
    ],
)
def test_schema_mismatch(config, recorded, mutation, msg):
    data = copy.deepcopy(recorded)
    mutation(data)
    with pytest.raises(ClassifyError, match=msg):
        parse_response(data, config.questions)
