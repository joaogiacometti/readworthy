#!/usr/bin/env python3
"""Run every text in <profile>/eval/<label>/*.txt through Jev with <profile>/readworthy.toml.

Prints accuracy, a confusion matrix, the rule checks behind every unsure or
wrong label, and the total cost. Needs OPENROUTER_API_KEY; costs money.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from readworthy.core import build_classifier
from readworthy.errors import ClassifyError
from readworthy.rules import LABELS


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

    samples = [(label, p) for label in LABELS for p in sorted((fixtures / label).glob("*.txt"))]
    counts = Counter(label for label, _ in samples)
    # unsure/ is optional: an unsure result is the model's doubt, not a kind of content.
    thin = [label for label in LABELS if label != "unsure" and counts[label] < 3]
    if thin:
        print(f"warning: fewer than 3 samples for: {', '.join(thin)}", file=sys.stderr)
    if not samples:
        print(f"eval: no texts found in {fixtures}/<label>/", file=sys.stderr)
        return 1

    confusion: Counter[tuple[str, str]] = Counter()
    wrong = []
    cost = 0.0
    # Each sample is an independent API call; run them concurrently, report in order.
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(clf.classify, path.read_text(encoding="utf-8")) for _, path in samples]
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
        confusion[expected, r.label] += 1
        mark = "ok " if r.label == expected else "BAD"
        print(f"{mark} {path.relative_to(fixtures)}  -> {r.label}")
        if r.label == "unsure" or r.label != expected:
            print("      checks: " + r.checks_text())
        if r.label != expected:
            wrong.append((path, expected, r))

    correct = sum(n for (e, p), n in confusion.items() if e == p)
    print(f"\naccuracy: {correct}/{len(samples)} = {correct / len(samples):.0%}")

    width = max(len(i) for i in LABELS) + 2
    print("\nconfusion (rows = expected, cols = predicted):")
    print(" " * width + "".join(f"{i:>{width}}" for i in LABELS))
    for e in LABELS:
        print(f"{e:<{width}}" + "".join(f"{confusion[e, p]:>{width}}" for p in LABELS))

    if wrong:
        print("\nmisclassified:")
        for path, expected, r in wrong:
            print(f"- {path.relative_to(fixtures)}: expected {expected}, got {r.label}")
            print("    " + path.read_text(encoding="utf-8").strip().replace("\n", " ")[:200] + "…")

    print(f"\ntotal cost: ${cost:.6f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
