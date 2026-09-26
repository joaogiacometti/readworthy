"""Classifier: text -> Jev answers -> read/skip/unsure (via rules) -> Result."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from readworthy.config import Config, load_config
from readworthy.errors import ClassifyError
from readworthy.jev import JevBackend
from readworthy.rules import decide

# Jev's context window is 32k tokens for state + questions. English prose is ~4 chars/token, so this leaves room.
MAX_INPUT_CHARS = 50_000


@dataclass(frozen=True)
class Result:
    label: str  # read, skip or unsure
    checks: dict[str, float]  # rule tests: test name -> probability
    cost_usd: float

    def checks_text(self) -> str:
        return ", ".join(f"{k}={v:.2f}" for k, v in self.checks.items())


class Classifier:
    def __init__(self, backend: JevBackend, config: Config):
        self.backend = backend
        self.config = config

    def classify(self, text: str) -> Result:
        """Classify `text`, cut to MAX_INPUT_CHARS."""
        text = text.strip()
        if not text:
            raise ClassifyError("input text is empty")
        decision = self.backend.decide({"content": text[:MAX_INPUT_CHARS]}, self.config.questions)
        label, checks = decide(decision.answers, self.config.rules)
        return Result(label, checks, decision.cost_usd)


def build_classifier(config_path: str | Path | None = None) -> Classifier:
    """A Jev classifier configured from `config_path` (else READWORTHY_CONFIG) and the environment (OPENROUTER_API_KEY,
    READWORTHY_MODEL)."""
    config = load_config(config_path)
    return Classifier(JevBackend.from_env(), config)
