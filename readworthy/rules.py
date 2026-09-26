"""Turn Jev's answers into read, skip or unsure, with the profile's skip rule.

The profile says what to skip, as an expression over its questions:

    skip = "promotion or (not teaches and (kind in [rant, drama] or hype))"

A noul question is a check by its id; a choice question is a check as
`id in [option, ...]`, whose probability is the total of those options. Tests
combine with `not`, `and`, `or` and parentheses, with the usual precedence.

Skipping is the only decision that loses something, so it has to be sure:
content the rule surely skips is skip, content it surely keeps is read, and
when a borderline answer could have changed that, the label is unsure and the
reader looks for themselves.

Every check is a probability compared against one `yes` threshold. A check
inside the uncertain band is unknown (None), and tests combine in
three-valued logic: `a and b` is surely false when either is surely false, so
a borderline `a` doesn't matter next to a confident `b`.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from readworthy.config import Rules
    from readworthy.jev import Answer

LABELS = ("read", "skip", "unsure")
KEYWORDS = frozenset({"and", "or", "not", "in"})


@dataclass(frozen=True)
class Check:
    question: str
    options: tuple[str, ...] = ()  # choice: the options that count as yes; empty for a noul

    def __str__(self) -> str:
        return f"{self.question} in [{', '.join(self.options)}]" if self.options else self.question


@dataclass(frozen=True)
class Not:
    arg: Expr


@dataclass(frozen=True)
class And:
    args: tuple[Expr, ...]


@dataclass(frozen=True)
class Or:
    args: tuple[Expr, ...]


Expr = Check | Not | And | Or

_TOKEN_RE = re.compile(r"\s*(?:([()\[\],])|([a-z][a-z0-9_]*))")


def parse(text: str) -> Expr:
    """Parse a skip rule; raises ValueError saying what is wrong."""
    return _Parser(text).parse()


class _Parser:
    def __init__(self, text: str):
        self.tokens: list[str] = []
        pos = 0
        while pos < len(text.rstrip()):
            m = _TOKEN_RE.match(text, pos)
            if not m or m.end() == pos:
                raise ValueError(f"unexpected {text[pos:].strip()[:20]!r}")
            self.tokens.append(m.group(1) or m.group(2))
            pos = m.end()
        self.i = 0

    def parse(self) -> Expr:
        expr = self._or()
        if self.i < len(self.tokens):
            raise ValueError(f"unexpected {self.tokens[self.i]!r}")
        return expr

    def _peek(self) -> str | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def _take(self, expected: str | None = None) -> str:
        tok = self._peek()
        if tok is None:
            raise ValueError(f"expected {expected!r} but the rule ended" if expected else "the rule ended too soon")
        if expected is not None and tok != expected:
            raise ValueError(f"expected {expected!r}, got {tok!r}")
        self.i += 1
        return tok

    def _word(self) -> str:
        tok = self._take()
        if not tok[0].isalpha() or tok in KEYWORDS:
            raise ValueError(f"expected a name, got {tok!r}")
        return tok

    def _or(self) -> Expr:
        args = [self._and()]
        while self._peek() == "or":
            self._take()
            args.append(self._and())
        return args[0] if len(args) == 1 else Or(tuple(args))

    def _and(self) -> Expr:
        args = [self._not()]
        while self._peek() == "and":
            self._take()
            args.append(self._not())
        return args[0] if len(args) == 1 else And(tuple(args))

    def _not(self) -> Expr:
        if self._peek() == "not":
            self._take()
            return Not(self._not())
        if self._peek() == "(":
            self._take()
            expr = self._or()
            self._take(")")
            return expr
        question = self._word()
        if self._peek() != "in":
            return Check(question)
        self._take()
        self._take("[")
        options = [self._word()]
        while self._peek() == ",":
            self._take()
            options.append(self._word())
        self._take("]")
        return Check(question, tuple(options))


def checks_of(expr: Expr) -> Iterator[Check]:
    """Every check in `expr`, in order of appearance."""
    if isinstance(expr, Check):
        yield expr
    elif isinstance(expr, Not):
        yield from checks_of(expr.arg)
    else:
        for arg in expr.args:
            yield from checks_of(arg)


def _not(a: bool | None) -> bool | None:
    return None if a is None else not a


def _and(a: bool | None, b: bool | None) -> bool | None:
    if a is False or b is False:
        return False
    return None if a is None or b is None else True


def _or(a: bool | None, b: bool | None) -> bool | None:
    return _not(_and(_not(a), _not(b)))


def _evaluate(expr: Expr, known: dict[str, bool | None]) -> bool | None:
    if isinstance(expr, Check):
        return known[str(expr)]
    if isinstance(expr, Not):
        return _not(_evaluate(expr.arg, known))
    combine = _and if isinstance(expr, And) else _or
    result = _evaluate(expr.args[0], known)
    for arg in expr.args[1:]:
        result = combine(result, _evaluate(arg, known))
    return result


def _probability(check: Check, answer: Answer) -> float:
    # The config checked that choice tests are on choice questions, and noul tests on nouls.
    if check.options:
        return sum(answer.probabilities[o] for o in check.options)  # type: ignore[union-attr]
    return answer.noul  # type: ignore[union-attr]


def decide(answers: dict[str, Answer], rules: Rules) -> tuple[str, dict[str, float]]:
    """The label, and the probability behind each check (check name -> probability)."""
    checks = {str(c): round(_probability(c, answers[c.question]), 4) for c in checks_of(rules.skip)}
    lo, hi = rules.uncertain_band
    known = {name: None if lo < p < hi else p >= rules.yes for name, p in checks.items()}
    skip = _evaluate(rules.skip, known)
    label = "unsure" if skip is None else "skip" if skip else "read"
    return label, checks
