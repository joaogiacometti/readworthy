#!/usr/bin/env python3
"""Run every text in <profile>/eval/{keep,archive}/*.txt through Jev with <profile>/readworthy.toml.

Each file's first line is the title and the rest the content, sent to Jev as separate fields like the webhook does.

Prints accuracy, a confusion matrix, Jev's `wanted` probability behind every wrong outcome, how each `keep_at` would
have scored, and the cost. Needs OPENROUTER_API_KEY; costs money, except for the answers cached in
<profile>/.eval-cache.json from earlier runs (keyed by model, question and text; --fresh ignores it).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from readworthy.config import Question
from readworthy.core import build_classifier
from readworthy.errors import ClassifyError
from readworthy.jev import Decision, JevBackend, NoulAnswer, question_body

OUTCOMES = ("keep", "archive")
THRESHOLDS = [round(0.30 + 0.05 * i, 2) for i in range(13)]  # 0.30 .. 0.90


class CachedBackend:
    """A JevBackend that answers from `path` when it has seen the same model, state and questions before.

    Cached answers cost nothing, so `Decision.cost_usd` is 0 for them. `jev-latest` is cached by that name: run with
    --fresh after it changes.
    """

    def __init__(self, backend: JevBackend, path: Path, fresh: bool = False):
        self.backend = backend
        self.path = path
        self.entries: dict[str, dict[str, float]] = {}
        if not fresh and path.exists():
            self.entries = json.loads(path.read_text(encoding="utf-8"))
        self.hits = 0
        self._lock = threading.Lock()

    def decide(self, state: Any, questions: dict[str, Question]) -> Decision:
        key = self._key(state, questions)
        with self._lock:
            cached = self.entries.get(key)
            if cached is not None and cached.keys() == questions.keys():
                self.hits += 1
                return Decision({qid: NoulAnswer(p) for qid, p in cached.items()}, 0.0)
        decision = self.backend.decide(state, questions)
        with self._lock:
            self.entries[key] = {qid: a.noul for qid, a in decision.answers.items()}
        return decision

    def save(self) -> None:
        self.path.write_text(json.dumps(self.entries, indent=0, sort_keys=True) + "\n", encoding="utf-8")

    def _key(self, state: Any, questions: dict[str, Question]) -> str:
        asked = {qid: question_body(q) for qid, q in questions.items()}
        raw = json.dumps({"model": self.backend.model, "state": state, "questions": asked}, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()


def read_sample(path: Path) -> tuple[str, str]:
    """(content, title): the first line is the title, the rest the content."""
    title, _, content = path.read_text(encoding="utf-8").partition("\n")
    return content, title


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=Path("profile"), help="profile folder (default: profile)")
    parser.add_argument("--fresh", action="store_true", help="ask Jev again instead of reusing cached answers")
    args = parser.parse_args()
    fixtures = args.profile / "eval"

    try:
        clf = build_classifier(args.profile / "readworthy.toml")
    except ClassifyError as e:
        print(f"eval: error: {e}", file=sys.stderr)
        return 1
    cache = CachedBackend(clf.backend, args.profile / ".eval-cache.json", fresh=args.fresh)
    clf.backend = cache

    samples = [(outcome, p) for outcome in OUTCOMES for p in sorted((fixtures / outcome).glob("*.txt"))]
    counts = Counter(outcome for outcome, _ in samples)
    thin = [outcome for outcome in OUTCOMES if counts[outcome] < 3]
    if thin:
        print(f"warning: fewer than 3 samples for: {', '.join(thin)}", file=sys.stderr)
    if not samples:
        print(f"eval: no texts found in {fixtures}/<keep|archive>/", file=sys.stderr)
        return 1

    confusion: Counter[tuple[str, str]] = Counter()
    wrong = []
    cost = 0.0
    # Each sample is an independent API call; run them concurrently, report in order.
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(clf.classify, *read_sample(path)) for _, path in samples]
        results = []
        for (_, path), future in zip(samples, futures, strict=True):
            try:
                results.append(future.result())
            except ClassifyError as e:
                pool.shutdown(cancel_futures=True)
                cache.save()  # keep what was already paid for
                print(f"eval: error on {path}: {e}", file=sys.stderr)
                return 1
    cache.save()
    for (expected, path), r in zip(samples, results, strict=True):
        cost += r.cost_usd
        got = "archive" if r.archive else "keep"
        confusion[expected, got] += 1
        mark = "ok " if got == expected else "BAD"
        print(f"{mark} {path.relative_to(fixtures)}  -> {got} (wanted={r.wanted:.2f})")
        if got != expected:
            wrong.append((path, expected, got))

    correct = sum(n for (e, p), n in confusion.items() if e == p)
    print(f"\naccuracy: {correct}/{len(samples)} = {correct / len(samples):.0%}")

    width = max(len(o) for o in OUTCOMES) + 2
    print("\nconfusion (rows = expected, cols = predicted):")
    print(" " * width + "".join(f"{o:>{width}}" for o in OUTCOMES))
    for e in OUTCOMES:
        print(f"{e:<{width}}" + "".join(f"{confusion[e, p]:>{width}}" for p in OUTCOMES))

    if wrong:
        print("\nmisclassified:")
        for path, expected, got in wrong:
            print(f"- {path.relative_to(fixtures)}: expected {expected}, got {got}")
            print("    " + path.read_text(encoding="utf-8").strip().replace("\n", " ")[:200] + "…")

    scored = [(expected, r.wanted) for (expected, _), r in zip(samples, results, strict=True)]
    print_thresholds(scored, clf.config.keep_at)

    print(f"\ncost: ${cost:.6f} ({cache.hits}/{len(samples)} answers from the cache, free)")
    return 0


def print_thresholds(scored: list[tuple[str, float]], keep_at: float) -> None:
    """How every keep_at in THRESHOLDS (plus the configured one) would have scored, from the same answers."""
    keeps = sum(1 for expected, _ in scored if expected == "keep")
    archives = len(scored) - keeps
    print("\nby keep_at (keep when wanted >= keep_at):")
    print("  keep_at  accuracy  good archived  unwanted kept")
    for t in sorted({*THRESHOLDS, keep_at}):
        lost = sum(1 for expected, wanted in scored if expected == "keep" and wanted < t)
        leaked = sum(1 for expected, wanted in scored if expected == "archive" and wanted >= t)
        right = len(scored) - lost - leaked
        mark = "  <- yours" if t == keep_at else ""
        print(f"  {t:>7.2f}  {right / len(scored):>8.0%}  {f'{lost}/{keeps}':>13}  {f'{leaked}/{archives}':>13}{mark}")


if __name__ == "__main__":
    sys.exit(main())
