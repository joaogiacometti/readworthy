"""Load and validate the profile's readworthy.toml: the topics you like and dislike, and the one question built from
them."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from readworthy.errors import ClassifyError, is_number

DEFAULT_CONFIG_PATH = "profile/readworthy.toml"
QUESTION_ID = "wanted"
KEYS = frozenset({"like", "dislike", "keep_at"})


class ConfigError(ClassifyError):
    """readworthy.toml is missing or invalid."""


@dataclass(frozen=True)
class Question:
    """A yes/no (noul) question for Jev."""

    id: str
    instructions: str
    criteria: dict[str, str]  # {"true": ..., "false": ...}


@dataclass(frozen=True)
class Config:
    like: tuple[str, ...]
    dislike: tuple[str, ...]
    keep_at: float  # keep when P(wanted) >= keep_at, else archive

    @property
    def question(self) -> Question:
        """The one question asked about every bookmark: is it about a like, and not mainly a dislike?

        Both halves are in the question itself: asked only about the topic, Jev keeps a rant about a liked topic.
        """
        likes = _listing(self.like)
        yes = (
            "Explains, reports or calmly argues something about one of the topics above, including announcements of "
            "releases, products or features that tell the reader what changed and how it works."
        )
        if not self.dislike:
            return Question(
                QUESTION_ID,
                f"Judging by `title` and `content`, is it mainly about one of: {likes}?",
                {"true": yes, "false": "Mainly about something else."},
            )
        dislikes = _listing(self.dislike)
        return Question(
            QUESTION_ID,
            f"Judging by `title` and `content`, is it mainly about one of: {likes}; and not mainly one of: {dislikes}?",
            {
                "true": yes,
                "false": f"Mainly one of: {dislikes}, even when it is about a topic above; or mainly about "
                "something else.",
            },
        )


def _listing(items: tuple[str, ...]) -> str:
    """ "a", "a or b", "a, b, or c"."""
    if len(items) <= 2:
        return " or ".join(items)
    return f"{', '.join(items[:-1])}, or {items[-1]}"


def load_config(path: str | Path | None = None) -> Config:
    """Load `path`, else $READWORTHY_CONFIG, else profile/readworthy.toml."""
    path = Path(path or os.environ.get("READWORTHY_CONFIG") or DEFAULT_CONFIG_PATH)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(
            f"config file not found: {path} (copy example-profile/ to profile/, or set READWORTHY_CONFIG)"
        ) from None
    except OSError as e:
        raise ConfigError(f"cannot read config file {path}: {e.strerror}") from None
    except UnicodeDecodeError:
        raise ConfigError(f"config file {path} is not valid UTF-8") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"invalid TOML in {path}: {e}") from None
    return parse_config(data, source=str(path))


def parse_config(data: dict[str, Any], source: str = "config") -> Config:
    unknown = set(data) - KEYS
    if unknown:
        raise ConfigError(f"{source}: unknown keys {sorted(unknown)}")
    like = _topics(data.get("like"), "like", source)
    if not like:
        raise ConfigError(f"{source}: like must list at least one topic")
    dislike = _topics(data.get("dislike", []), "dislike", source)
    seen: set[str] = set()
    for topic in like + dislike:
        if topic.casefold() in seen:
            raise ConfigError(f"{source}: topic {topic!r} is listed twice")
        seen.add(topic.casefold())
    keep_at = data.get("keep_at", 0.5)
    if not is_number(keep_at) or not 0 <= keep_at <= 1:
        raise ConfigError(f"{source}: keep_at must be a number between 0 and 1")
    return Config(like, dislike, float(keep_at))


def _topics(raw: Any, key: str, source: str) -> tuple[str, ...]:
    if not isinstance(raw, list) or not all(isinstance(t, str) and t.strip() for t in raw):
        raise ConfigError(f'{source}: {key} must be a list of topics, such as ["math", "science"]')
    return tuple(t.strip() for t in raw)
