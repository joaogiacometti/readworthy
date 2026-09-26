"""Load and validate the profile's readworthy.toml: the questions sent to Jev and the skip rule over them."""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from readworthy.errors import ClassifyError, is_number
from readworthy.rules import KEYWORDS, Expr, checks_of, parse

DEFAULT_CONFIG_PATH = "profile/readworthy.toml"
_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
QUESTION_TYPES = ("noul", "choice")


class ConfigError(ClassifyError):
    """readworthy.toml is missing or invalid."""


@dataclass(frozen=True)
class Question:
    id: str
    type: str  # "choice" | "noul"
    instructions: str
    criteria: Any = None  # choice: {option: description}, noul: {"true", "false"} or None

    @property
    def options(self) -> list[str]:
        """Choice option names."""
        return list(self.criteria) if self.type == "choice" else []


@dataclass(frozen=True)
class Rules:
    yes: float
    uncertain_band: tuple[float, float]
    skip: Expr


@dataclass(frozen=True)
class Config:
    questions: dict[str, Question]
    rules: Rules


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
    raw_questions = data.get("questions")
    if not isinstance(raw_questions, dict):
        raise ConfigError(f"{source}: needs [questions.<id>] tables")
    if not raw_questions:
        raise ConfigError(f"{source}: needs at least one [questions.<id>] table")
    questions = {qid: _parse_question(qid, q, source) for qid, q in raw_questions.items()}
    return Config(questions=questions, rules=_parse_rules(data.get("rules"), questions, source))


def _parse_question(qid: str, q: Any, source: str) -> Question:
    where = f"{source}: question {qid!r}"
    if not _ID_RE.match(qid) or qid in KEYWORDS:
        raise ConfigError(f"{where}: id must match {_ID_RE.pattern} and not be one of {sorted(KEYWORDS)}")
    if not isinstance(q, dict):
        raise ConfigError(f"{where} must be a table")
    unknown = set(q) - {"type", "instructions", "criteria"}
    if unknown:
        raise ConfigError(f"{where}: unknown keys {sorted(unknown)}")
    qtype = q.get("type")
    if qtype not in QUESTION_TYPES:
        raise ConfigError(f"{where}: type must be one of {list(QUESTION_TYPES)}")
    instructions = q.get("instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        raise ConfigError(f"{where}: needs non-empty 'instructions'")
    criteria = q.get("criteria")
    if qtype == "choice":
        if not isinstance(criteria, dict) or len(criteria) < 2:
            raise ConfigError(f"{where}: choice criteria must be a table with at least 2 options")
        for option in criteria:
            if not _ID_RE.match(option) or option in KEYWORDS:
                raise ConfigError(
                    f"{where}: option {option!r} must match {_ID_RE.pattern} and not be one of {sorted(KEYWORDS)}"
                )
    elif criteria is not None and (not isinstance(criteria, dict) or set(criteria) != {"true", "false"}):
        raise ConfigError(f"{where}: noul criteria must have exactly 'true' and 'false'")
    return Question(qid, qtype, instructions, criteria)


def _parse_rules(raw: Any, questions: dict[str, Question], source: str) -> Rules:
    where = f"{source}: [rules]"
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} table is missing")

    yes = raw.get("yes")
    if not is_number(yes) or not 0 <= yes <= 1:
        raise ConfigError(f"{where}: yes must be a number between 0 and 1")

    band = raw.get("uncertain_band")
    if not (isinstance(band, list) and len(band) == 2 and all(map(is_number, band)) and 0 <= band[0] <= band[1] <= 1):
        raise ConfigError(f"{where}: uncertain_band must be [low, high] with 0 <= low <= high <= 1")

    text = raw.get("skip")
    if not isinstance(text, str) or not text.strip():
        raise ConfigError(f'{where}: skip must be a rule such as "promotion or not teaches"')
    try:
        skip = parse(text)
    except ValueError as e:
        raise ConfigError(f"{where}: invalid skip rule: {e}") from None

    used = set()
    for check in checks_of(skip):
        q = questions.get(check.question)
        if q is None:
            raise ConfigError(f"{where}: skip uses {check.question!r}, which is not a question")
        used.add(q.id)
        if q.type == "noul" and check.options:
            raise ConfigError(f"{where}: {check.question!r} is a noul question; use it without 'in [...]'")
        if q.type == "choice":
            if not check.options:
                raise ConfigError(f"{where}: {check.question!r} is a choice question; use '{q.id} in [option, ...]'")
            unknown = [o for o in check.options if o not in q.options]
            if unknown:
                raise ConfigError(f"{where}: {unknown} in {check} are not options of {q.id!r}")
            if len(set(check.options)) != len(check.options):
                raise ConfigError(f"{where}: options in {check} must not repeat")
            if len(check.options) == len(q.options):
                raise ConfigError(f"{where}: {check} must leave out at least one option of {q.id!r}")
    unused = sorted(set(questions) - used)
    if unused:
        raise ConfigError(f"{where}: questions {unused} are not used by skip; remove them or use them")

    return Rules(yes=float(yes), uncertain_band=(float(band[0]), float(band[1])), skip=skip)
