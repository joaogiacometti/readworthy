import pytest
from conftest import EXAMPLE_PROFILE
from fake import FakeBackend

from readworthy.core import MAX_INPUT_CHARS, Classifier, build_classifier
from readworthy.errors import ClassifyError
from readworthy.jev import NoulAnswer


def test_state_is_title_and_content(config):
    fake = FakeBackend()
    Classifier(fake, config).classify("  hello \n", " Hi ")
    assert fake.calls[-1][0] == {"title": "Hi", "content": "hello"}
    assert fake.calls[-1][1] == {"wanted": config.question}


def test_truncation(config):
    fake = FakeBackend()
    Classifier(fake, config).classify("y" * (MAX_INPUT_CHARS + 1))
    assert fake.calls[-1][0] == {"title": "", "content": "y" * MAX_INPUT_CHARS}


@pytest.mark.parametrize("wanted, archive", [(0.9, False), (0.5, False), (0.49, True), (0.0, True)])
def test_archives_below_keep_at(config, wanted, archive):
    r = Classifier(FakeBackend({"wanted": NoulAnswer(wanted)}), config).classify("text")
    assert r.archive is archive and r.wanted == wanted and r.cost_usd == 0.0


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
    assert build_classifier().config.keep_at == 0.5
