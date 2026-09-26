import json
from pathlib import Path

import pytest

from readworthy.config import load_config

TESTS = Path(__file__).resolve().parent
EXAMPLE_PROFILE = TESTS.parent / "example-profile"
FIXTURES = TESTS / "fixtures"


@pytest.fixture(scope="session")
def config():
    """Tests use the generic example profile, never a personal one."""
    return load_config(EXAMPLE_PROFILE / "readworthy.toml")


@pytest.fixture(scope="session")
def recorded():
    """A real Decisions API response to the example profile's questions."""
    return json.loads((FIXTURES / "jev" / "decision_response.json").read_text())


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in (
        "OPENROUTER_API_KEY",
        "READWORTHY_MODEL",
        "READWORTHY_CONFIG",
        "READWORTHY_WEBHOOK_TOKEN",
        "KARAKEEP_URL",
        "KARAKEEP_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
