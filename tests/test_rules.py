import dataclasses

import pytest

from readworthy.jev import ChoiceAnswer, NoulAnswer, parse_response
from readworthy.rules import And, Check, Not, Or, decide, parse

SKIP_KINDS = "kind in [rant, drama]"


def kind(config, **probs):
    return ChoiceAnswer({o: probs.get(o, 0.0) for o in config.questions["kind"].options})


def answers(config, **overrides):
    """Defaults describe an honest explainer that sells nothing. `kind` may be an option name, meaning surely it."""
    base = {
        "teaches": NoulAnswer(0.9),
        "kind": kind(config, explanation=1.0),
        "promotion": NoulAnswer(0.05),
        "hype": NoulAnswer(0.02),
    }
    if isinstance(overrides.get("kind"), str):
        overrides["kind"] = kind(config, **{overrides["kind"]: 1.0})
    return base | overrides


def label(config, **overrides):
    return decide(answers(config, **overrides), config.rules)[0]


def test_read(config):
    assert decide(answers(config), config.rules) == (
        "read",
        {"promotion": 0.05, "teaches": 0.9, SKIP_KINDS: 0, "hype": 0.02},
    )


def test_promotion_is_skip_even_when_it_teaches(config):
    assert label(config, promotion=NoulAnswer(0.9)) == "skip"


@pytest.mark.parametrize("k", ["rant", "drama"])
def test_skip_kind_that_teaches_nothing_is_skip(config, k):
    assert label(config, teaches=NoulAnswer(0.1), kind=k) == "skip"


@pytest.mark.parametrize("k", ["rant", "drama"])
def test_skip_kind_that_teaches_is_read(config, k):
    assert label(config, kind=k) == "read"


def test_hype_that_teaches_nothing_is_skip(config):
    assert label(config, teaches=NoulAnswer(0.1), kind="news", hype=NoulAnswer(0.9)) == "skip"


@pytest.mark.parametrize("k", ["news", "opinion", "other"])
def test_other_kinds_that_teach_nothing_are_read(config, k):
    assert label(config, teaches=NoulAnswer(0.1), kind=k) == "read"


def test_skip_kinds_add_up(config):
    # No single skip kind wins, but together they do.
    a = answers(config, teaches=NoulAnswer(0.1), kind=kind(config, news=0.2, rant=0.4, drama=0.4))
    lbl, checks = decide(a, config.rules)
    assert lbl == "skip" and checks[SKIP_KINDS] == 0.8


def test_borderline_hype_does_not_matter_next_to_sure_kind(config):
    assert label(config, teaches=NoulAnswer(0.1), kind="drama", hype=NoulAnswer(0.5)) == "skip"


def test_borderline_promotion_is_unsure(config):
    assert label(config, promotion=NoulAnswer(0.5)) == "unsure"


def test_borderline_teaches_with_skip_kind_is_unsure(config):
    assert label(config, teaches=NoulAnswer(0.45), kind="rant") == "unsure"


def test_borderline_teaches_does_not_matter_when_nothing_else_would_skip(config):
    assert label(config, teaches=NoulAnswer(0.45), kind="news", hype=NoulAnswer(0.1)) == "read"


def test_borderline_kind_is_unsure(config):
    a = answers(config, teaches=NoulAnswer(0.1), kind=kind(config, news=0.5, rant=0.5))
    assert decide(a, config.rules)[0] == "unsure"


def test_borderline_kind_does_not_matter_next_to_sure_hype(config):
    a = answers(config, teaches=NoulAnswer(0.1), kind=kind(config, news=0.5, rant=0.5), hype=NoulAnswer(0.95))
    assert decide(a, config.rules)[0] == "skip"


def test_borderline_hype_is_unsure(config):
    assert label(config, teaches=NoulAnswer(0.1), kind="news", hype=NoulAnswer(0.6)) == "unsure"


def test_recorded_response_is_read(config, recorded):
    assert decide(parse_response(recorded, config.questions).answers, config.rules)[0] == "read"


def test_rule_comes_from_the_profile(config):
    # Same answers, another reader: this one skips all news.
    rules = dataclasses.replace(config.rules, skip=parse("kind in [news]"))
    assert decide(answers(config, kind="news"), rules) == ("skip", {"kind in [news]": 1.0})
    assert decide(answers(config), rules)[0] == "read"


def test_parse_precedence():
    a, b, c = Check("a"), Check("b"), Check("c")
    assert parse("a or b and not c") == Or((a, And((b, Not(c)))))
    assert parse("(a or b) and c") == And((Or((a, b)), c))
    assert parse("not not a") == Not(Not(a))
    assert parse(" k in [x,y] ") == Check("k", ("x", "y"))
    assert str(Check("k", ("x", "y"))) == "k in [x, y]"


@pytest.mark.parametrize(
    "text, msg",
    [
        ("a or", "ended"),
        ("a b", "unexpected 'b'"),
        ("(a", "expected '\\)'"),
        ("a and or b", "expected a name"),
        ("k in []", "expected a name"),
        ("k in [x", "expected ']'"),
        ("a & b", "unexpected '& b'"),
        ("A", "unexpected 'A'"),
    ],
)
def test_parse_errors(text, msg):
    with pytest.raises(ValueError, match=msg):
        parse(text)
