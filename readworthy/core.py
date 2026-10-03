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
# Jev first reads only this much; on 242 labelled texts the first 2k alone settled 3 in 4 of them and the cascade
# matched reading 12k every time, for 40% less.
FIRST_LOOK_CHARS = 2_000
# A first look between keep_at - 0.4 and keep_at + 0.25 (0.2-0.85 at keep_at 0.6, where it was measured) is unsure:
# Jev reads up to MAX_INPUT_CHARS before deciding. Wider costs more for the same answers; narrower missed keeps.
UNSURE_BELOW = 0.4
UNSURE_ABOVE = 0.25


@dataclass(frozen=True)
class Result:
    archive: bool
    wanted: float  # probability that it is about a like and not mainly a dislike
    cost_usd: float  # of every request made for it


class Classifier:
    def __init__(self, backend: JevBackend, config: Config):
        self.backend = backend
        self.config = config

    def classify(self, content: str, title: str = "") -> Result:
        """Classify `content` under `title` (separate fields for Jev): its first FIRST_LOOK_CHARS, then, if Jev is
        unsure, up to MAX_INPUT_CHARS."""
        content = content.strip()
        if not content:
            raise ClassifyError("input text is empty")
        title = title.strip()
        wanted, cost = self._ask(title, content[:FIRST_LOOK_CHARS])
        if len(content) > FIRST_LOOK_CHARS and self._unsure(wanted):
            wanted, more = self._ask(title, content[:MAX_INPUT_CHARS])
            cost += more
        return Result(wanted < self.config.keep_at, wanted, cost)

    def _ask(self, title: str, content: str) -> tuple[float, float]:
        question = self.config.question
        decision = self.backend.decide({"title": title, "content": content}, {question.id: question})
        return round(decision.answers[question.id].noul, 4), decision.cost_usd

    def _unsure(self, wanted: float) -> bool:
        keep_at = self.config.keep_at
        return round(keep_at - UNSURE_BELOW, 4) < wanted < round(keep_at + UNSURE_ABOVE, 4)


def build_classifier(config_path: str | Path | None = None) -> Classifier:
    """A Jev classifier configured from `config_path` (else READWORTHY_CONFIG) and the environment (OPENROUTER_API_KEY,
    READWORTHY_MODEL)."""
    config = load_config(config_path)
    return Classifier(JevBackend.from_env(), config)
