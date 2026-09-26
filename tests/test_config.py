import copy
import tomllib

import pytest
from conftest import EXAMPLE_PROFILE

from readworthy.config import ConfigError, load_config, parse_config
from readworthy.rules import LABELS

BASE = tomllib.loads((EXAMPLE_PROFILE / "readworthy.toml").read_text())


def mutate(fn):
    data = copy.deepcopy(BASE)
    fn(data)
    return data


def test_example_profile(config):
    assert config.questions["kind"].type == "choice"
    assert "other" in config.questions["kind"].options


def test_example_eval_folders_are_labels():
    folders = {p.name for p in (EXAMPLE_PROFILE / "eval").iterdir()}
    assert folders <= set(LABELS)
    assert all(len(list((EXAMPLE_PROFILE / "eval" / f).glob("*.txt"))) >= 3 for f in ("read", "skip"))


def test_question_without_criteria():
    data = mutate(lambda d: d["questions"]["hype"].pop("criteria"))
    assert parse_config(data).questions["hype"].criteria is None


@pytest.mark.parametrize(
    "fn, msg",
    [
        (lambda d: d.pop("questions"), "questions"),
        (lambda d: d.update(questions={}), "at least one"),
        (lambda d: d["questions"].update(Bad={"type": "noul", "instructions": "x"}), "id must match"),
        (lambda d: d["questions"].update({"and": {"type": "noul", "instructions": "x"}}), "id must match"),
        (lambda d: d["questions"].update(extra={"type": "noul", "instructions": "x"}), r"\['extra'\] are not used"),
        (lambda d: d["questions"]["hype"].update(type="text"), "type must be one of"),
        (lambda d: d["questions"]["kind"].update(extra=1), "unknown keys"),
        (lambda d: d["questions"]["kind"].pop("instructions"), "instructions"),
        (lambda d: d["questions"]["kind"].update(criteria={"only": "one"}), "at least 2"),
        (lambda d: d["questions"]["kind"].update(criteria={"Bad-Name": "x", "b": "y"}), "option"),
        (lambda d: d["questions"]["hype"].update(criteria={"yes": "x"}), "'true' and 'false'"),
        (lambda d: d.pop("rules"), r"\[rules\] table is missing"),
        (lambda d: d["rules"].update(yes=1.5), "yes must be"),
        (lambda d: d["rules"].update(uncertain_band=[0.7, 0.3]), "uncertain_band"),
        (lambda d: d["rules"].pop("skip"), "skip must be a rule"),
        (lambda d: d["rules"].update(skip=" "), "skip must be a rule"),
        (lambda d: d["rules"].update(skip="promotion or"), "invalid skip rule"),
        (lambda d: d["questions"].pop("teaches"), "'teaches', which is not a question"),
        (lambda d: d["rules"].update(skip=d["rules"]["skip"].replace("hype", "hype in [x]")), "noul question"),
        (
            lambda d: d["rules"].update(skip=d["rules"]["skip"].replace("kind in [rant, drama]", "kind")),
            "choice question",
        ),
        (lambda d: d["rules"].update(skip=d["rules"]["skip"].replace("drama", "cooking")), "not options"),
        (lambda d: d["rules"].update(skip=d["rules"]["skip"].replace("drama", "rant")), "must not repeat"),
        (
            lambda d: d["rules"].update(
                skip=d["rules"]["skip"].replace("rant, drama", ", ".join(d["questions"]["kind"]["criteria"]))
            ),
            "leave out at least one",
        ),
    ],
)
def test_invalid_config(fn, msg):
    with pytest.raises(ConfigError, match=msg):
        parse_config(mutate(fn))


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")


def test_bad_toml(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text("this is = = not toml")
    with pytest.raises(ConfigError, match="invalid TOML"):
        load_config(p)


def test_unreadable_file(tmp_path):
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path)
    p = tmp_path / "c.toml"
    p.write_bytes(b"x = '\xff'")
    with pytest.raises(ConfigError, match="not valid UTF-8"):
        load_config(p)


def test_path_from_env_else_profile(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError, match="profile/readworthy.toml"):
        load_config()
    monkeypatch.setenv("READWORTHY_CONFIG", str(EXAMPLE_PROFILE / "readworthy.toml"))
    assert load_config().rules.yes == 0.5


def test_questions_are_the_profiles():
    def fn(d):
        d["questions"] = {"paywalled": {"type": "noul", "instructions": "Is `content` only a paywall teaser?"}}
        d["rules"]["skip"] = "paywalled"

    config = parse_config(mutate(fn))
    assert list(config.questions) == ["paywalled"] and str(config.rules.skip) == "paywalled"
