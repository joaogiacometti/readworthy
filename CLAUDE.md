# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Karakeep webhook, and only that: each crawled link or new text note is archived in Karakeep unless its main topic is one the user likes. It asks Jev (TypeSafe's decision model, via OpenRouter's alpha `POST /api/alpha/decisions` endpoint) one noul question built in code from the profile's `like` and `dislike` topic lists, and archives when the probability is below `keep_at`. The topics are the user's choice (profile); the question wording and the logic are fixed (code). `README.md` is the user-facing description.

## Commands

All tooling comes from the Nix flake. Run `nix develop` first (it sets `PYTHONPATH` to the repo and loads `.env`).

```sh
pytest                                   # unit tests, offline
pytest tests/test_core.py::test_name     # single test
ruff check . && ruff format --check .    # lint (line length 120, py313)
nix flake check                          # tests + lint together (what CI runs)
nix build .#image                        # OCI image
python -m readworthy.api                 # serves POST /karakeep/webhook only
```

`python scripts/eval.py [--profile DIR]` hits the real API (needs `OPENROUTER_API_KEY`, costs money; run it only when asked). It runs `DIR/eval/{keep,archive}/*.txt` (first line the title, the rest the content) through Jev with `DIR/readworthy.toml`.

## Layout

- `readworthy/api.py`: FastAPI app, built once at startup by `app_from_env` (missing settings → exit 1). Checks the `READWORTHY_WEBHOOK_TOKEN` bearer token, parses the raw body with `parse_event` (422 if not JSON or malformed, 200 `ignored` unless it's a crawled link or a created text note), replies 202 and hands the id to `ArchiveQueue`. Keep logic out of it.
- `readworthy/karakeep.py`: `KarakeepClient` is the only code that talks to Karakeep; it validates bookmark ids before building URLs. `classify_bookmark` skips bookmarks that are already archived, otherwise fetches `GET /bookmarks/{id}/content?format=markdown` (one request, up to `MAX_INPUT_CHARS`, images stripped), classifies it (title and content as separate state fields), and archives it (`PATCH /bookmarks/{id}` `{"archived": true}`) when `Result.archive`, logging the outcome. `ArchiveQueue` runs it on 2 worker threads of its own, merging events for a bookmark that is already pending or running; failures are only logged.
- `readworthy/core.py`: `Classifier.classify` → `backend.decide({"title": title, "content": content}, {"wanted": config.question})` → `Result` (`archive` = `wanted < keep_at`, `wanted`, `cost_usd`).
- `readworthy/jev.py`: the only code that talks to OpenRouter (retries on 429 and 5xx honouring `Retry-After`; `question_body` builds the request's questions), plus the answer type (`NoulAnswer`). Only noul questions are asked. API changes should be a one-file fix here.
- `readworthy/config.py`: loads `$READWORTHY_CONFIG`, else `profile/readworthy.toml`, and validates it strictly: `like` (non-empty list of topics), optional `dislike`, optional `keep_at` (default 0.5), no other keys, no topic in both lists. `Config.question` builds the one `wanted` noul question from them: about a like, and not mainly a dislike (both halves in one question; asked about the topic alone, Jev keeps rants about a liked topic).

## Profiles and privacy

- `profile/` is git-ignored and holds the owner's personal profile (`readworthy.toml` + `eval/`). Never commit it, and never copy its wording, examples or texts into tracked files.
- `example-profile/` is the committed, deliberately generic example. The image bakes it in at `/profile/readworthy.toml`.
- Tests use `example-profile/readworthy.toml` only, never `profile/`. `tests/fixtures/jev/decision_response.json` is a recorded Jev response, trimmed to the single `wanted` noul answer.
- `OPENROUTER_API_KEY`, `KARAKEEP_API_KEY` and `READWORTHY_WEBHOOK_TOKEN` come from the environment only, never written to files.

## Tests

Fully offline: `tests/fake.py` (`FakeBackend`), the recorded Jev response, `tests/helpers.backend_with` / `backend_replaying` (a `JevBackend` over `httpx.MockTransport`), and `FakeKarakeep` / `link` in `tests/fake.py`. `tests/helpers.py` also has `classifier`, `failing_backend` and the `ARCHIVE`/`KEEP` answers. `conftest.py` clears the `READWORTHY_*`, `KARAKEEP_*` and `OPENROUTER_API_KEY` env vars for every test.

## Errors

Any failure raises `ClassifyError` (in `readworthy/errors.py`; `ConfigError` and `KarakeepError` are subclasses) with a user-facing message. Never fall back to a guessed outcome.
