import pytest
from conftest import EXAMPLE_PROFILE
from fake import FakeBackend

from readworthy.core import FIRST_LOOK_CHARS, MAX_INPUT_CHARS, Classifier, Result, build_classifier
from readworthy.errors import ClassifyError
from readworthy.jev import Decision, NoulAnswer


def test_state_is_title_and_content(config):
    fake = FakeBackend()
    Classifier(fake, config).classify("  hello \n", " Hi ")
    assert fake.calls[-1][0] == {"title": "Hi", "content": "hello"}
    assert fake.calls[-1][1] == {"wanted": config.question}


class SequenceBackend(FakeBackend):
    """Answers `wanted` with the next probability in `nouls`, each costing `cost`."""

    def __init__(self, *nouls, cost=0.001):
        super().__init__()
        self.nouls = iter(nouls)
        self.cost = cost

    def decide(self, state, questions):
        super().decide(state, questions)
        return Decision({"wanted": NoulAnswer(next(self.nouls))}, self.cost)


def test_first_look_only_when_sure(config):
    fake = SequenceBackend(0.95)
    r = Classifier(fake, config).classify("y" * (MAX_INPUT_CHARS + 1), "T")
    assert [s for s, _ in fake.calls] == [{"title": "T", "content": "y" * FIRST_LOOK_CHARS}]
    assert r == Result(False, 0.95, 0.001)


def test_reads_further_when_unsure(config):
    fake = SequenceBackend(0.6, 0.3)
    r = Classifier(fake, config).classify("y" * (MAX_INPUT_CHARS + 1))
    assert [s["content"] for s, _ in fake.calls] == ["y" * FIRST_LOOK_CHARS, "y" * MAX_INPUT_CHARS]
    assert r == Result(True, 0.3, 0.002)


def test_short_text_is_asked_once_even_when_unsure(config):
    fake = SequenceBackend(0.5)
    Classifier(fake, config).classify("y" * FIRST_LOOK_CHARS)
    assert len(fake.calls) == 1


# The example profile's keep_at is 0.5, so the unsure band is 0.1-0.75, both ends excluded.
@pytest.mark.parametrize("first, asks", [(0.1, 1), (0.11, 2), (0.5, 2), (0.74, 2), (0.75, 1), (0.0, 1), (1.0, 1)])
def test_unsure_band_follows_keep_at(config, first, asks):
    fake = SequenceBackend(first, 0.5)
    Classifier(fake, config).classify("y" * (FIRST_LOOK_CHARS + 1))
    assert len(fake.calls) == asks


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
