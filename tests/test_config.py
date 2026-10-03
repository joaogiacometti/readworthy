import copy
import tomllib

import pytest
from conftest import EXAMPLE_PROFILE

from readworthy.config import ConfigError, load_config, parse_config

BASE = tomllib.loads((EXAMPLE_PROFILE / "readworthy.toml").read_text())


def mutate(fn):
    data = copy.deepcopy(BASE)
    fn(data)
    return data


def test_example_profile(config):
    assert config.like and config.dislike and config.keep_at == 0.5


def test_example_eval_folders():
    folders = {p.name for p in (EXAMPLE_PROFILE / "eval").iterdir()}
    assert folders == {"keep", "archive"}
    assert all(len(list((EXAMPLE_PROFILE / "eval" / f).glob("*.txt"))) >= 3 for f in folders)


def test_question():
    q = parse_config({"like": ["knitting", " gardening ", "baking"], "dislike": ["ads", "rants"]}).question
    assert q.id == "wanted"
    assert q.instructions == (
        "Is `content` mainly about one of: knitting, gardening, or baking; and not mainly one of: ads or rants?"
    )
    assert q.criteria["true"].startswith("Explains, reports or calmly argues something about one of the topics above")
    assert q.criteria["false"].startswith("Mainly one of: ads or rants, even when it is about a topic above;")


def test_question_without_dislikes():
    config = parse_config({"like": ["knitting"]})
    assert config.dislike == () and config.keep_at == 0.5
    assert config.question.instructions == "Is `content` mainly about one of: knitting?"
    assert config.question.criteria["false"] == "Mainly about something else."


@pytest.mark.parametrize(
    "fn, msg",
    [
        (lambda d: d.pop("like"), "like must be a list"),
        (lambda d: d.update(like=[]), "at least one topic"),
        (lambda d: d.update(like="recipes"), "like must be a list"),
        (lambda d: d.update(like=["recipes", " "]), "like must be a list"),
        (lambda d: d.update(dislike=[1]), "dislike must be a list"),
        (lambda d: d.update(dislike=["Recipes"]), "'Recipes' is listed twice"),
        (lambda d: d.update(keep_at=1.5), "keep_at must be"),
        (lambda d: d.update(keep_at=True), "keep_at must be"),
        (lambda d: d.update(skip="x"), r"unknown keys \['skip'\]"),
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
    assert load_config().keep_at == 0.5
