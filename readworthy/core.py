"""Classifier: text -> Jev's answer to "is it about a like, and not mainly a dislike?" -> keep or archive."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from readworthy.config import Config, load_config
from readworthy.errors import ClassifyError
from readworthy.jev import JevBackend

# About 3k tokens: the topic is clear by then (same eval score as 50k), and long posts cost ~4x less. Jev's context
# window is 32k tokens.
MAX_INPUT_CHARS = 12_000


@dataclass(frozen=True)
class Result:
    archive: bool
    wanted: float  # probability that it is about a like and not mainly a dislike
    cost_usd: float


class Classifier:
    def __init__(self, backend: JevBackend, config: Config):
        self.backend = backend
        self.config = config

    def classify(self, content: str, title: str = "") -> Result:
        """Classify `content`, cut to MAX_INPUT_CHARS, under `title`. Jev gets them as separate fields."""
        content = content.strip()
        if not content:
            raise ClassifyError("input text is empty")
        question = self.config.question
        state = {"title": title.strip(), "content": content[:MAX_INPUT_CHARS]}
        decision = self.backend.decide(state, {question.id: question})
        wanted = round(decision.answers[question.id].noul, 4)
        return Result(wanted < self.config.keep_at, wanted, decision.cost_usd)


def build_classifier(config_path: str | Path | None = None) -> Classifier:
    """A Jev classifier configured from `config_path` (else READWORTHY_CONFIG) and the environment (OPENROUTER_API_KEY,
    READWORTHY_MODEL)."""
    config = load_config(config_path)
    return Classifier(JevBackend.from_env(), config)
