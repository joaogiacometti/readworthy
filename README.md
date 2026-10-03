# readworthy

A [Karakeep](https://karakeep.app) webhook that archives each new bookmark whose main topic isn't one you like. You list the topics you like and the ones you don't; everything else is left alone, so your reading list is simply your non-archived bookmarks.

## How it decides

For each crawled link or new text note, readworthy asks [Jev](https://openrouter.ai/blog/insights/what-is-jev/) (TypeSafe's decision model, via OpenRouter) one yes/no question built from your profile:

> Judging by `title` and `content`, is it mainly about one of: recipes, cooking techniques, or fair tests of ingredients or equipment; and not mainly one of: sales pitches, rants, feuds and gossip, or hype?

Both halves are in the one question, so a feud between chefs is a "no" even though it's about cooking. (Asked only about the topic, Jev says yes to rants about a topic you like.) Jev returns the probability of "yes". When it is at least `keep_at` (default 0.5), the bookmark is kept; otherwise it is archived. A bookmark that matches neither list is archived too.

If anything fails (Karakeep, Jev, a bookmark with no text), the error is logged and nothing is archived. It never guesses.

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
| `READWORTHY_MODEL` | default `~typesafe/jev-latest`; pin a snapshot for reproducible results |

The service stops at startup if a setting is missing or the profile is invalid. It only serves `POST /karakeep/webhook`, replies at once, and classifies in the background. Archived bookmarks are left alone. Any other bookmark is judged again when it is re-crawled, so if you unarchive one and re-crawl it, it may be archived again. Content past 50,000 characters is cut off.

## Make it yours

Everything personal lives in one git-ignored folder, `profile/`:

```sh
cp -r example-profile profile
```

- `profile/readworthy.toml` holds three settings:

  ```toml
  like = ["recipes", "cooking techniques"]   # at least one
  dislike = ["sales pitches", "rants"]       # optional
  keep_at = 0.5                              # optional; raise it to archive more, lower it to archive less
  ```

  A topic can be any short phrase. The same topic can't be in both lists.
- `profile/eval/keep/` and `profile/eval/archive/` hold texts you've sorted yourself, at least 3 each, one per `.txt` file: the title on the first line, then the content. `python scripts/eval.py` runs them through Jev and prints accuracy, a confusion matrix, Jev's probability for each text, how each `keep_at` would have scored, and the cost. It needs `OPENROUTER_API_KEY` and costs a little; answers are cached in `profile/.eval-cache.json`, so a re-run only pays for texts, topics or a model it hasn't seen (`--fresh` asks again, e.g. after `jev-latest` changes). Where it's wrong, make a topic more specific or add a dislike.

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

To release, bump `version` in `pyproject.toml`, then push a tag `vX.Y.Z`. GitHub Actions builds `ghcr.io/<owner>/readworthy:X.Y.Z` and `:latest` for amd64 and arm64, then creates the GitHub release with notes generated from the commits.
