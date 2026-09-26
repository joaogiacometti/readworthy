"""Builders shared by several test modules."""

import httpx
from fake import FakeBackend

from readworthy.core import Classifier
from readworthy.jev import JevBackend, NoulAnswer

SKIP = {"promotion": NoulAnswer(1.0)}
# Borderline promotion could make it skip.
UNSURE = {"promotion": NoulAnswer(0.5)}


def classifier(config, answers=None):
    return Classifier(FakeBackend(answers), config)


def backend_replaying(*responses, sleeps=None):
    """A backend whose HTTP calls get `responses` in order."""
    it = iter(responses)
    return backend_with(lambda r: next(it), sleeps)


def backend_with(handler, sleeps=None):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return JevBackend("sk-test", client=client, sleep=(sleeps.append if sleeps is not None else lambda s: None))


def failing_backend():
    """A backend whose every call gets HTTP 500."""
    return backend_with(lambda r: httpx.Response(500, text="oops"))
