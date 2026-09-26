"""The error every module raises, and a number check shared by the validators."""

from __future__ import annotations

from typing import Any


class ClassifyError(Exception):
    """Classification failed; the message is meant for the user."""


def is_number(v: Any) -> bool:
    """An int or float, but not a bool (which is an int subclass)."""
    return isinstance(v, (int, float)) and not isinstance(v, bool)
