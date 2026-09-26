import pytest
from conftest import EXAMPLE_PROFILE
from fake import FakeBackend

from readworthy.core import MAX_INPUT_CHARS, Classifier, build_classifier
from readworthy.errors import ClassifyError
from readworthy.jev import NoulAnswer


def test_state_is_content_object(config):
    fake = FakeBackend()
    Classifier(fake, config).classify("  hello \n")
    assert fake.calls[-1][0] == {"content": "hello"}


def test_truncation(config):
    fake = FakeBackend()
    Classifier(fake, config).classify("y" * (MAX_INPUT_CHARS + 1))
    assert fake.calls[-1][0] == {"content": "y" * MAX_INPUT_CHARS}


def test_labels_from_rules(config):
    r = Classifier(FakeBackend({"promotion": NoulAnswer(1.0)}), config).classify("buy now")
    assert r.label == "skip" and r.checks["promotion"] == 1.0 and r.cost_usd == 0.0


def test_empty_input(config):
    with pytest.raises(ClassifyError, match="empty"):
        Classifier(FakeBackend(), config).classify("  \n")


def test_build_classifier_errors(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ClassifyError, match="copy example-profile"):
        build_classifier()
    monkeypatch.setenv("READWORTHY_CONFIG", str(EXAMPLE_PROFILE / "readworthy.toml"))
    with pytest.raises(ClassifyError, match="OPENROUTER_API_KEY"):
        build_classifier()
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    assert build_classifier().config.rules.yes == 0.5
