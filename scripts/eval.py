#!/usr/bin/env python3
"""Run every text in <profile>/eval/{keep,archive}/*.txt through Jev with <profile>/readworthy.toml.

Each file's first line is the title and the rest the content, sent to Jev as separate fields like the webhook does.

Prints accuracy, a confusion matrix, Jev's `wanted` probability behind every wrong
outcome, and the total cost. Needs OPENROUTER_API_KEY; costs money.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from readworthy.core import build_classifier
from readworthy.errors import ClassifyError

OUTCOMES = ("keep", "archive")


def read_sample(path: Path) -> tuple[str, str]:
    """(content, title): the first line is the title, the rest the content."""
    title, _, content = path.read_text(encoding="utf-8").partition("\n")
    return content, title


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=Path("profile"), help="profile folder (default: profile)")
    args = parser.parse_args()
    fixtures = args.profile / "eval"

    try:
        clf = build_classifier(args.profile / "readworthy.toml")
    except ClassifyError as e:
        print(f"eval: error: {e}", file=sys.stderr)
        return 1

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
                print(f"eval: error on {path}: {e}", file=sys.stderr)
                return 1
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

    print(f"\ntotal cost: ${cost:.6f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
