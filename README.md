# readworthy

A [Karakeep](https://karakeep.app) webhook that tags each new bookmark with one label, depending on whether it's worth your reading time:

| tag | meaning |
|---|---|
| `readworthy/read` | not obviously a waste of time |
| `readworthy/skip` | obviously a waste, by your own rule (the example: a sales pitch, or something that teaches nothing and is only a rant, drama or hype) |
| `readworthy/unsure` | a borderline answer could have made it skip, so you decide |

Your reading feed is a Karakeep search for `-#readworthy/skip`, and your review list is `#readworthy/unsure`. What counts as waste is up to you: it's set in your [profile](#make-it-yours).

## How it decides

For each crawled link or new text note, readworthy asks [Jev](https://openrouter.ai/blog/insights/what-is-jev/) (TypeSafe's decision model, via OpenRouter) your profile's narrow questions about the content, all in one request. Each is a yes/no (`noul`) or a `choice`. The example profile asks four:

| question | type | asks |
|---|---|---|
| `teaches` | yes/no | does it teach how something works, a method, or a reasoned comparison? |
| `kind` | choice | tutorial, explanation, news, opinion, rant, drama, ... |
| `promotion` | yes/no | does it mainly exist to sell something? |
| `hype` | yes/no | does it make bold claims with nothing behind them? |

Jev returns probabilities, never text. Your profile's `skip` rule then picks the label, in code ([`readworthy/rules.py`](readworthy/rules.py)). The example's is:

```toml
skip = "promotion or (not teaches and (kind in [rant, drama] or hype))"
```

A yes/no question is a test by its id, a choice question a test as `id in [option, ...]` (the total probability of those options), and tests combine with `not`, `and`, `or` and parentheses. Each test passes at or above `yes` (default 0.5), and is unknown inside the uncertain band (default 0.35–0.65). Then:

- **skip** when the rule is surely true;
- **unsure** when an unknown test could have changed that;
- **read** otherwise.

Only skipping loses you anything, so it has to be sure. A borderline answer that can't change the outcome doesn't matter. If anything fails (Karakeep, Jev, a bookmark with no text), the error is logged and no tag is written. It never guesses a label.

A typical post costs well under $0.001. The text is sent to OpenRouter and TypeSafe, so only use readworthy on content you're fine sharing with them.

## Run it

It needs Karakeep v0.33.1 or later. Run the image next to Karakeep, for example in the same `docker compose` project:

```yaml
  readworthy:
    image: ghcr.io/joaogiacometti/readworthy:latest
    restart: unless-stopped
    environment:
      OPENROUTER_API_KEY: ${OPENROUTER_API_KEY}
      KARAKEEP_URL: http://web:3000          # Karakeep's own service, without /api/v1
      KARAKEEP_API_KEY: ${KARAKEEP_API_KEY}   # Karakeep → Settings → API keys
      READWORTHY_WEBHOOK_TOKEN: ${READWORTHY_WEBHOOK_TOKEN}
    volumes:
      - ./profile:/profile:ro                 # your profile; the image ships the example one
```

Then, in Karakeep:

1. Go to Settings → Webhooks and add `http://readworthy:8000/karakeep/webhook` with the same token, for the `crawled` and `created` events.
2. Karakeep blocks webhooks to private addresses, so add `CRAWLER_ALLOWED_INTERNAL_HOSTNAMES=readworthy` to the Karakeep `web` service's environment.

| setting | |
|---|---|
| `OPENROUTER_API_KEY` | required |
| `KARAKEEP_URL`, `KARAKEEP_API_KEY` | required |
| `READWORTHY_WEBHOOK_TOKEN` | required; any long random string, e.g. `openssl rand -hex 32` |
| `READWORTHY_CONFIG` | default `profile/readworthy.toml` (`/profile/readworthy.toml` in the image) |
| `READWORTHY_MODEL` | default `~typesafe/jev-latest`; pin a snapshot for reproducible labels |

The service stops at startup if a setting is missing or the profile is invalid. It only serves `POST /karakeep/webhook`, replies at once, and classifies in the background. Bookmarks that already have a `readworthy/*` label are left alone, so re-crawls cost nothing. To classify one again, remove its label and re-crawl it. Content past 50,000 characters is cut off.

## Make it yours

Everything personal lives in one git-ignored folder, `profile/`:

```sh
cp -r example-profile profile
```

- `profile/readworthy.toml` holds your questions (ids, types, wording, examples, choice options), the `skip` rule over them and the thresholds. Add, remove or rename questions freely; the profile is rejected if the rule names a question or option that doesn't exist, or if a question isn't used by the rule (it would only cost money). The comments at the top are for you: Jev never sees them.
- `profile/eval/read/` and `profile/eval/skip/` hold texts you've labelled yourself, at least 3 each. `python scripts/eval.py` runs them through Jev and prints accuracy, a confusion matrix, the checks behind every wrong or unsure label, and the cost. It needs `OPENROUTER_API_KEY` and costs a little. Where a label is wrong, the checks show which question to reword.

## Development

Tooling comes from the Nix flake.

```sh
cp .env.example .env           # then fill it in
nix develop                    # dev shell; loads .env (or `direnv allow`)
python -m readworthy.api         # the webhook on 127.0.0.1:8000
pytest                         # unit tests; offline, using example-profile/
ruff check . && ruff format --check .
nix flake check                # tests + lint, as CI runs them
nix build .#image              # the container image (docker load < result)
```

To release, bump `version` in `pyproject.toml`, then push a tag `vX.Y.Z`. GitHub Actions builds `ghcr.io/<owner>/readworthy:X.Y.Z` and `:latest` for amd64 and arm64.
