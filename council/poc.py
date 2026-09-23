"""Phase 1: prove three heads answer the same prompt, independently and in parallel.

Nothing here reviews, judges or synthesizes. The only question it answers is
whether we can get three independent answers, measure them, and survive one of
them failing.

    python -m council.poc "your question"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from .providers import CHECKED_ON, GenResult, build_heads

load_dotenv()

DEFAULT_PROMPT = "Explique em poucas linhas a diferença entre processo e thread."
OUT_DIR = Path("out")


async def run(prompt: str, timeout_s: float) -> list[GenResult]:
    """Fan out to every head at once. One head's failure is not the run's."""
    heads = build_heads()
    tasks = [asyncio.wait_for(h.generate(prompt), timeout=timeout_s) for h in heads]
    settled = await asyncio.gather(*tasks, return_exceptions=True)

    results: list[GenResult] = []
    for head, outcome in zip(heads, settled):
        if isinstance(outcome, BaseException):
            # Only the wait_for timeout lands here — generate() swallows the rest.
            results.append(
                GenResult(
                    head=head.head,
                    model=head.model,
                    via=head.via,
                    latency_ms=int(timeout_s * 1000),
                    error=f"{type(outcome).__name__}: timeout after {timeout_s}s",
                )
            )
        else:
            results.append(outcome)
    return results


def save(prompt: str, results: list[GenResult]) -> Path:
    run_dir = OUT_DIR / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=True)

    for result in results:
        path = run_dir / f"{result.head}.json"
        path.write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))

    known = [r.cost_usd for r in results if r.cost_usd is not None]
    summary = {
        "prompt": prompt,
        "prices_checked_on": CHECKED_ON,
        "heads_ok": sum(r.ok for r in results),
        "heads_total": len(results),
        # Partial by construction when a model is missing from PRICES.
        "total_cost_usd": round(sum(known), 6),
        "cost_is_partial": len(known) != len(results),
        "results": [r.to_dict() for r in results],
    }
    (run_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False)
    )
    return run_dir


def report(results: list[GenResult], run_dir: Path) -> None:
    print(f"\n{'head':<10} {'via':<10} {'model':<20} {'ms':>7} {'in':>7} {'out':>7} {'USD':>9}")
    print("-" * 75)
    for r in results:
        cost = "  n/a" if r.cost_usd is None else f"{r.cost_usd:.6f}"
        print(
            f"{r.head:<10} {r.via:<10} {r.model:<20} "
            f"{r.latency_ms:>7} {r.tokens_in:>7} {r.tokens_out:>7} {cost:>9}"
        )

    for r in results:
        if not r.ok:
            print(f"\n!! {r.head} failed: {r.error}")

    known = [r.cost_usd for r in results if r.cost_usd is not None]
    label = "total (partial)" if len(known) != len(results) else "total"
    print(f"\n{label}: ${sum(known):.6f}   prices checked {CHECKED_ON}")
    print(f"saved to: {run_dir}")

    ok = sum(r.ok for r in results)
    if ok < len(results):
        print(f"\nPARTIAL: {ok}/{len(results)} heads answered.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 1 council POC")
    parser.add_argument("prompt", nargs="?", default=DEFAULT_PROMPT)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()

    results = asyncio.run(run(args.prompt, args.timeout))
    run_dir = save(args.prompt, results)
    report(results, run_dir)

    # Non-zero when every head failed, so a shell loop can tell total failure
    # from a partial run.
    return 0 if any(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
