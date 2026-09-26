import json
import logging
import threading

import httpx
import pytest
from fake import FakeBackend, FakeKarakeep, karakeep_with, link
from helpers import SKIP, UNSURE, classifier, failing_backend

from readworthy.core import MAX_INPUT_CHARS, Classifier
from readworthy.errors import ClassifyError
from readworthy.karakeep import KarakeepClient, KarakeepError, TagQueue, bookmark_text, classify_bookmark, parse_event

# --- content ---


def test_bookmark_text():
    assert bookmark_text(link(), "Body text.") == "Title\n\nBody text."
    assert bookmark_text(link(title=None), " Body text.\n") == "Body text."
    assert bookmark_text({"id": "b", "content": {"type": "text", "text": "a note"}}, "a note") == "a note"
    assert bookmark_text(link(), "a\n\n\n\nb") == "Title\n\na\n\nb"


def test_bookmark_text_prefers_user_title():
    assert bookmark_text(link(user_title=" Mine "), "Body.") == "Mine\n\nBody."
    assert bookmark_text(link(user_title="  "), "Body.") == "Title\n\nBody."


@pytest.mark.parametrize("content", ["", " \n\n"])
def test_bookmark_text_empty(content):
    with pytest.raises(ClassifyError, match=r"bookmark b1 has no text to classify"):
        bookmark_text(link(), content)


def test_get_content():
    kk = FakeKarakeep(link(content="short"))
    assert kk.client.get_content("b1") == "short"
    assert kk.requests[0][1] == "/api/v1/bookmarks/b1/content"


def test_get_content_cut_short():
    kk = FakeKarakeep(link(content="x" * (MAX_INPUT_CHARS + 10)))
    assert kk.client.get_content("b1") == "x" * MAX_INPUT_CHARS


def test_get_content_drops_images():
    md = (
        'Intro\n\n![a [b]](data:image/png;base64,AAAA+/=)\n\n![](https://x/y.png "a (title)")\n\n'
        "More [link](https://z) and ![x](y)."
    )
    kk = FakeKarakeep(link(content=md))
    assert kk.client.get_content("b1") == "Intro\n\n\n\n\n\nMore [link](https://z) and ."


@pytest.mark.parametrize("body", [{"content": 1}, []])
def test_get_content_bad_response(body):
    client = karakeep_with(lambda r: httpx.Response(200, json=body))
    with pytest.raises(ClassifyError, match="unexpected response from Karakeep"):
        client.get_content("b1")


# --- classify_bookmark ---


def test_classify_bookmark_tags(config, caplog):
    kk = FakeKarakeep(link())
    fake = FakeBackend(SKIP)
    with caplog.at_level(logging.INFO, logger="readworthy.karakeep"):
        result = classify_bookmark(Classifier(fake, config), kk.client, "b1")
    assert result.label == "skip"
    assert (
        "b1 tagged readworthy/skip (promotion=1.00, teaches=0.00, kind in [rant, drama]=0.00, hype=0.00) $0.000000"
        in caplog.text
    )
    assert fake.calls[0][0] == {"content": "Title\n\nBody text."}
    assert [p for m, p, _ in kk.requests if m == "GET"] == ["/api/v1/bookmarks/b1", "/api/v1/bookmarks/b1/content"]
    assert kk.tag_writes() == [("POST", ["readworthy/skip"])]
    assert kk.requests[2][2]["tags"][0]["attachedBy"] == "human"


def test_classify_bookmark_unsure(config):
    kk = FakeKarakeep(link(tags=["mine"]))
    assert classify_bookmark(classifier(config, UNSURE), kk.client, "b1").label == "unsure"
    assert kk.tag_writes() == [("POST", ["readworthy/unsure"])]


@pytest.mark.parametrize("tag", ["readworthy/read", "readworthy/skip", "readworthy/unsure"])
def test_classify_bookmark_skips_labelled(config, tag):
    # A re-crawl of a labelled bookmark costs no Jev call and doesn't read the content.
    kk = FakeKarakeep(link(tags=[tag]))
    fake = FakeBackend(SKIP)
    assert classify_bookmark(Classifier(fake, config), kk.client, "b1") is None
    assert fake.calls == []
    assert [p for m, p, _ in kk.requests] == ["/api/v1/bookmarks/b1"]


def test_classify_bookmark_no_text(config):
    kk = FakeKarakeep(link(content=""))
    with pytest.raises(ClassifyError, match="no text to classify"):
        classify_bookmark(classifier(config), kk.client, "b1")
    assert kk.tag_writes() == []


@pytest.mark.parametrize("status, match", [(401, "HTTP 401 .*: nope"), (500, "HTTP 500")])
def test_karakeep_http_errors(config, status, match):
    kk = FakeKarakeep(link(), status=status)
    with pytest.raises(KarakeepError, match=match):
        classify_bookmark(classifier(config), kk.client, "b1")


def test_karakeep_not_found(config):
    kk = FakeKarakeep()
    with pytest.raises(ClassifyError, match="HTTP 404 for GET /bookmarks/zz: Bookmark not found"):
        classify_bookmark(classifier(config), kk.client, "zz")


def test_backend_error_writes_no_tags(config):
    kk = FakeKarakeep(link())
    c = Classifier(failing_backend(), config)
    with pytest.raises(ClassifyError, match="OpenRouter returned HTTP 500"):
        classify_bookmark(c, kk.client, "b1")
    assert kk.tag_writes() == []


# --- client ---


def test_client_requires_env():
    with pytest.raises(ClassifyError, match="KARAKEEP_URL"):
        KarakeepClient.from_env()
    with pytest.raises(ClassifyError, match="KARAKEEP_API_KEY"):
        KarakeepClient("https://keep.example", "")


@pytest.mark.parametrize(
    "url", ["keep.example.com", "ftp://keep.example.com", "http://", "https://k/?a=1", "http://k#x"]
)
def test_client_rejects_bad_url(url):
    with pytest.raises(ClassifyError, match="KARAKEEP_URL must look like"):
        KarakeepClient(url, "k")


@pytest.mark.parametrize("bookmark_id", ["", "x/../../users/me", "b1?x=1", "b1#", "a b", "é", "x" * 65])
def test_client_rejects_bad_bookmark_id(bookmark_id):
    kk = FakeKarakeep()
    with pytest.raises(ClassifyError, match="invalid Karakeep bookmark id"):
        kk.client.get_bookmark(bookmark_id)
    with pytest.raises(ClassifyError, match="invalid Karakeep bookmark id"):
        kk.client.attach_tag(bookmark_id, "readworthy/skip")
    assert kk.requests == []


@pytest.mark.parametrize("error", [httpx.ConnectError("refused"), httpx.InvalidURL("bad host")])
def test_client_transport_errors(error):
    def fail(request):
        raise error

    client = karakeep_with(fail)
    with pytest.raises(KarakeepError, match="request to Karakeep failed"):
        client.get_bookmark("b1")


# --- events ---


@pytest.mark.parametrize(
    "operation, kind, handled",
    [("crawled", "link", True), ("created", "text", True), ("created", "link", False), ("edited", "text", False)],
)
def test_parse_event(operation, kind, handled):
    event = parse_event(json.dumps({"bookmarkId": "b1", "operation": operation, "type": kind}).encode())
    assert event.bookmark_id == "b1" and event.handled is handled


@pytest.mark.parametrize("body", [{"operation": "crawled"}, {"bookmarkId": "x/../me"}, [], "b1"])
def test_parse_event_rejects(body):
    with pytest.raises(ClassifyError, match="bookmarkId"):
        parse_event(json.dumps(body).encode())


def test_parse_event_not_json():
    with pytest.raises(ClassifyError, match="not JSON"):
        parse_event(b"{nope")


# --- TagQueue ---


class Runs:
    """A run function for TagQueue that blocks until released and records what ran."""

    def __init__(self, config):
        self.config = config
        self.fail = None
        self.started: list[str] = []
        self.threads: list[str] = []
        self.release = threading.Event()
        self.started_one = threading.Semaphore(0)

    def __call__(self, bookmark_id):
        self.started.append(bookmark_id)
        self.threads.append(threading.current_thread().name)
        self.started_one.release()
        assert self.release.wait(5)
        if self.fail:
            raise self.fail
        return classifier(self.config, SKIP).classify("x")

    def wait_started(self):
        assert self.started_one.acquire(timeout=5)


@pytest.fixture
def runs(config):
    return Runs(config)


def test_queue_runs_on_its_own_threads(runs):
    q = TagQueue(runs, workers=1)
    assert q.submit("b1") == "queued"
    runs.release.set()
    assert q.join(5)
    assert runs.started == ["b1"]
    assert runs.threads[0].startswith("readworthy")
    q.close()


def test_queue_merges_events_for_a_pending_bookmark(runs):
    q = TagQueue(runs, workers=2)
    assert q.submit("b1") == "queued"
    runs.wait_started()
    assert q.submit("b1") == "merged"  # running: never twice at once
    assert q.submit("b2") == "queued"
    runs.release.set()
    assert q.join(5)
    assert sorted(runs.started) == ["b1", "b2"]
    assert q.submit("b1") == "queued"  # done, so a later event runs again
    assert q.join(5)
    q.close()


@pytest.mark.parametrize(
    "error, logged",
    [
        (ClassifyError("Karakeep is down"), "bookmark b1 not classified: Karakeep is down"),
        (TypeError("bug"), "bookmark b1 not classified: unexpected error"),
    ],
)
def test_queue_logs_failures_and_keeps_going(runs, caplog, error, logged):
    runs.fail = error
    runs.release.set()
    q = TagQueue(runs, workers=1)
    with caplog.at_level(logging.ERROR, logger="readworthy.karakeep"):
        q.submit("b1")
        assert q.join(5)
    assert logged in caplog.text
    runs.fail = None
    q.submit("b2")
    assert q.join(5)
    assert runs.started == ["b1", "b2"]
    q.close()
