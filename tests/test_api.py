import logging

import pytest
from conftest import EXAMPLE_PROFILE
from fake import FakeKarakeep, link
from fastapi.testclient import TestClient
from helpers import ARCHIVE, classifier, failing_backend

from readworthy import api
from readworthy.core import Classifier
from readworthy.errors import ClassifyError

EVENT = {"jobId": "1", "type": "link", "bookmarkId": "b1", "userId": "u", "url": "x", "operation": "crawled"}
AUTH = {"Authorization": "Bearer secret"}


class Webhook:
    """A fresh app with token "secret". Calling it posts to the webhook and waits for the queued work to finish."""

    def __init__(self, classifier, karakeep):
        self.app = api.create_app(classifier, karakeep, "secret")
        self.client = TestClient(self.app)

    def __call__(self, body=EVENT, headers=AUTH, **kw):
        r = self.client.post("/karakeep/webhook", json=body, headers=headers, **kw)
        assert self.app.state.archive_queue.join(5)
        return r


@pytest.fixture
def kk():
    return FakeKarakeep(link())


@pytest.fixture
def post(config, kk):
    return Webhook(classifier(config, ARCHIVE), kk.client)


def test_webhook_archives_bookmark(post, kk):
    r = post()
    assert r.status_code == 202 and r.json() == {"status": "queued"}
    assert kk.writes() == [("PATCH", "/api/v1/bookmarks/b1", {"archived": True})]


def test_webhook_skips_archived_bookmark(config, caplog):
    kk = FakeKarakeep(link(archived=True))
    with caplog.at_level(logging.INFO):
        assert Webhook(classifier(config, ARCHIVE), kk.client)().status_code == 202
    assert "bookmark b1 already archived" in caplog.text
    assert kk.writes() == []


def test_webhook_ignores_other_events(post, kk):
    r = post({**EVENT, "operation": "edited"})
    assert r.status_code == 200 and r.json() == {"status": "ignored"}
    assert kk.requests == []


@pytest.mark.parametrize("header", [None, "secret", "Basic secret", "Bearer", "Bearer wrong", "Bearersecret"])
def test_webhook_rejects_bad_authorization(post, kk, header):
    r = post(headers={"Authorization": header} if header else {})
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"
    assert kk.requests == []


@pytest.mark.parametrize("scheme", ["bearer", "BEARER"])
def test_webhook_auth_scheme_is_case_insensitive(post, scheme):
    assert post(headers={"Authorization": f"{scheme} secret"}).status_code == 202


@pytest.mark.parametrize("body", [{"operation": "crawled"}, {**EVENT, "bookmarkId": "x/../../users/me?"}, [EVENT]])
def test_webhook_bad_body(post, kk, body):
    assert post(body).status_code == 422
    assert kk.requests == []


def test_webhook_not_json(post):
    assert post(None, content=b"{nope").status_code == 422


def test_webhook_failure_is_logged(config, kk, caplog):
    failing = Classifier(failing_backend(), config)
    with caplog.at_level(logging.ERROR):
        assert Webhook(failing, kk.client)().status_code == 202
    assert "bookmark b1 not classified: OpenRouter returned HTTP 500" in caplog.text
    assert kk.writes() == []


def test_serves_only_the_webhook(post):
    for path in ("/", "/docs", "/redoc", "/openapi.json"):
        assert post.client.get(path).status_code == 404, path


def test_needs_a_token(config, kk):
    with pytest.raises(ClassifyError, match="READWORTHY_WEBHOOK_TOKEN"):
        api.create_app(classifier(config), kk.client, "")


def test_main_fails_at_start_without_settings(monkeypatch, capsys):
    monkeypatch.setenv("READWORTHY_CONFIG", str(EXAMPLE_PROFILE / "readworthy.toml"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    ran = []
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: ran.append(kw))
    assert api.main([]) == 1
    assert "readworthy: error: KARAKEEP_URL is not set" in capsys.readouterr().err
    monkeypatch.setenv("KARAKEEP_URL", "https://keep.example")
    monkeypatch.setenv("KARAKEEP_API_KEY", "k")
    assert api.main([]) == 1
    assert "READWORTHY_WEBHOOK_TOKEN is not set" in capsys.readouterr().err
    monkeypatch.setenv("READWORTHY_WEBHOOK_TOKEN", "t")
    assert api.main(["--port", "9000"]) == 0
    assert ran == [{"host": "127.0.0.1", "port": 9000}]
