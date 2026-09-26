# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Karakeep webhook, and only that: each crawled link or new text note gets one tag, `readworthy/read`, `readworthy/skip` or `readworthy/unsure`. It asks Jev (TypeSafe's decision model, via OpenRouter's alpha `POST /api/alpha/decisions` endpoint) the profile's narrow typed questions, then picks the label by evaluating the profile's `skip` rule in code. What to skip is the user's choice (profile); the labels and the logic are fixed (code). `README.md` is the user-facing description.

## Commands

All tooling comes from the Nix flake. Run `nix develop` first (it sets `PYTHONPATH` to the repo and loads `.env`).

```sh
pytest                                   # unit tests, offline
pytest tests/test_rules.py::test_name    # single test
ruff check . && ruff format --check .    # lint (line length 120, py313)
nix flake check                          # tests + lint together (what CI runs)
nix build .#image                        # OCI image
python -m readworthy.api                 # serves POST /karakeep/webhook only
```

`python scripts/eval.py [--profile DIR]` hits the real API (needs `OPENROUTER_API_KEY`, costs money; run it only when asked). It runs `DIR/eval/<label>/*.txt` through Jev with `DIR/readworthy.toml`.

## Layout

- `readworthy/api.py`: FastAPI app, built once at startup by `app_from_env` (missing settings → exit 1). Checks the `READWORTHY_WEBHOOK_TOKEN` bearer token, parses the raw body with `parse_event` (422 if not JSON or malformed, 200 `ignored` unless it's a crawled link or a created text note), replies 202 and hands the id to `TagQueue`. Keep logic out of it.
- `readworthy/karakeep.py`: `KarakeepClient` is the only code that talks to Karakeep; it validates bookmark ids before building URLs. `classify_bookmark` skips bookmarks that already have a `readworthy/<label>` tag, otherwise fetches `GET /bookmarks/{id}/content?format=markdown` (one request, up to `MAX_INPUT_CHARS`, images stripped), classifies title + content, and attaches the tag as `human`, logging the outcome. `TagQueue` runs it on 2 worker threads of its own, merging events for a bookmark that is already pending or running; failures are only logged.
- `readworthy/core.py`: `Classifier.classify` → `backend.decide({"content": text}, questions)` → `rules.decide` → `Result` (`label`, `checks`, `cost_usd`).
- `readworthy/jev.py`: the only code that talks to OpenRouter (retries on 429/529 honouring `Retry-After`; `question_body` builds the request's questions), plus the answer types (`ChoiceAnswer`, `NoulAnswer`). API changes should be a one-file fix here.
- `readworthy/rules.py`: parses the profile's `skip` rule (`parse` → `Check`/`Not`/`And`/`Or`; a noul is `id`, a choice is `id in [opt, ...]`) and evaluates it. Each check is a probability compared against one `yes` threshold. Checks inside the uncertain band are unknown (`None`) and combine in three-valued logic (`_and`/`_or`/`_not`). Surely skip → `skip`, surely not → `read`, unknown → `unsure`. `checks` maps each check's text to its probability. Must not import `config`/`jev` at runtime (config imports it).
- `readworthy/config.py`: loads `$READWORTHY_CONFIG`, else `profile/readworthy.toml`, and validates it strictly: questions are any ids of type `noul`/`choice`, and the `skip` rule must only name existing questions and options, use every question, and give each choice check a strict subset of its options.

## Profiles and privacy

- `profile/` is git-ignored and holds the owner's personal profile (`readworthy.toml` + `eval/`). Never commit it, and never copy its wording, examples or texts into tracked files.
- `example-profile/` is the committed, deliberately generic example. The image bakes it in at `/profile/readworthy.toml`.
- Tests use `example-profile/readworthy.toml` only, never `profile/`. `tests/fixtures/jev/decision_response.json` is a real Jev response whose `kind` options match the example profile; if you change those options, update the fixture's `kind.probabilities` keys to match.
- `OPENROUTER_API_KEY`, `KARAKEEP_API_KEY` and `READWORTHY_WEBHOOK_TOKEN` come from the environment only, never written to files.

## Tests

Fully offline: `tests/fake.py` (`FakeBackend`), the recorded Jev response, `tests/helpers.backend_with` / `backend_replaying` (a `JevBackend` over `httpx.MockTransport`), and `FakeKarakeep` / `link` in `tests/fake.py`. `tests/helpers.py` also has `classifier`, `failing_backend` and the `SKIP`/`UNSURE` answers. `conftest.py` clears the `READWORTHY_*`, `KARAKEEP_*` and `OPENROUTER_API_KEY` env vars for every test.

## Errors

Any failure raises `ClassifyError` (in `readworthy/errors.py`; `ConfigError` and `KarakeepError` are subclasses) with a user-facing message. Never fall back to a guessed label.
