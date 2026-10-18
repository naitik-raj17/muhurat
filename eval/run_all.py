"""Run the whole offline evaluation and write eval/reports/REPORT.md (+ results.json).

    make infra && make eval
    # or: cd backend && PYTHONPATH=.. uv run python -m eval.run_all

EVAL_EMBEDDING_PROVIDER=hash runs faster, with weaker vector search than the real model.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from typing import Any

from eval import (
    contracts,
    cost,
    harness,  # sets up the environment before `app` is imported
    latency,
    rsvp,
    vendor_matching,
)
from eval.report import render


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            cwd=harness.BACKEND_DIR,
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


async def _run() -> dict[str, Any]:
    timings: dict[str, float] = {}
    started = time.perf_counter()
    counts = await harness.seed()
    timings["seed"] = time.perf_counter() - started
    harness.pin_quiet_hours_open()

    results: dict[str, Any] = {}
    async with harness.api_client() as client:
        for name, module in (
            ("vendor_matching", vendor_matching),
            ("contracts", contracts),
            ("rsvp", rsvp),
            ("latency", latency),
            ("cost", cost),
        ):
            print(f"running {name} ...", flush=True)
            t = time.perf_counter()
            results[name] = await module.run(client)
            timings[name] = time.perf_counter() - t
    results["meta"] = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "commit": _git_commit(),
        "database": harness.EVAL_DB,
        "embedding_provider": os.environ["EMBEDDING_PROVIDER"],
        "llm": "mock (offline; rule-based extractors, token counts priced at real model rates)",
        "seed": counts,
        "timings_s": timings,
    }
    return results


def main() -> int:
    print(f"preparing {harness.EVAL_DB} ...", flush=True)
    harness.prepare_database()
    results = asyncio.run(_run())
    harness.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (harness.REPORT_DIR / "results.json").write_text(
        json.dumps(results, indent=2, default=str, ensure_ascii=False) + "\n"
    )
    report = render(results)
    (harness.REPORT_DIR / "REPORT.md").write_text(report)
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
