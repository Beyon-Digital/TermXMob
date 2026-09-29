#!/usr/bin/env python3
"""Run the Agent benchmark scenarios and write JSON evidence.

Usage:
    uv run python scripts/agent_bench.py [--out plans/agent-v2/benchmarks/after.json] [--scenario name ...]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from bench_agent import SCENARIOS, run_all  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="", help="output JSON path (default: stdout only)")
    parser.add_argument("--scenario", action="append", default=[])
    parser.add_argument("--label", default="")
    args = parser.parse_args()
    base = Path(tempfile.mkdtemp(prefix="termx-bench-"))
    started = time.time()
    results = asyncio.run(run_all(base, args.scenario or None))
    report = {
        "label": args.label,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_s": round(time.time() - started, 2),
        "scenarios": results,
    }
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {out}")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
