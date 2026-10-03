"""The service: POST /karakeep/webhook classifies crawled bookmarks and archives the ones you don't want.

Run: readworthy [--host HOST] [--port PORT].
"""

from __future__ import annotations

import argparse
import functools
import hmac
import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from readworthy.core import Classifier, build_classifier
from readworthy.errors import ClassifyError
from readworthy.karakeep import ArchiveQueue, KarakeepClient, classify_bookmark, parse_event

log = logging.getLogger("readworthy.api")

# auto_error=False so a missing or malformed header reaches the handler, which logs why it rejects the request.
_webhook_auth = HTTPBearer(auto_error=False)


def create_app(classifier: Classifier, karakeep: KarakeepClient, token: str) -> FastAPI:
    if not token:
        raise ClassifyError("READWORTHY_WEBHOOK_TOKEN is not set")
    queue = ArchiveQueue(functools.partial(classify_bookmark, classifier, karakeep))

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await run_in_threadpool(queue.close)

    app = FastAPI(title="readworthy", lifespan=lifespan, openapi_url=None)  # no Swagger UI: Karakeep is the only client
    app.state.archive_queue = queue

    @app.post("/karakeep/webhook", status_code=202)
    async def karakeep_webhook(
        request: Request,
        auth: Annotated[HTTPAuthorizationCredentials | None, Depends(_webhook_auth)],
    ) -> dict:
        """Queue crawled links and new text notes to be classified, and archived unless you like their topic.

        Replies at once (Karakeep times out after 5 s); `status` is `queued`, or `merged` when the bookmark is
        already being classified.
        """
        if auth is None or not hmac.compare_digest(auth.credentials.encode(), token.encode()):
            log.error("webhook rejected (HTTP 401): invalid webhook token")  # Karakeep only logs the status code
            raise HTTPException(status_code=401, detail="invalid webhook token", headers={"WWW-Authenticate": "Bearer"})
        try:
            event = parse_event(await request.body())
        except ClassifyError as e:
            raise HTTPException(status_code=422, detail=str(e)) from None
        if not event.handled:
            return JSONResponse({"status": "ignored"}, status_code=200)
        return {"status": queue.submit(event.bookmark_id)}

    return app


def app_from_env() -> FastAPI:
    return create_app(build_classifier(), KarakeepClient.from_env(), os.environ.get("READWORTHY_WEBHOOK_TOKEN", ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="readworthy", description="Serve the Karakeep webhook.")
    parser.add_argument("--host", default="127.0.0.1", help="interface to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="port to listen on (default: 8000)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per Karakeep/OpenRouter request is noise
    try:
        app = app_from_env()
    except ClassifyError as e:
        print(f"readworthy: error: {e}", file=sys.stderr)
        return 1
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
