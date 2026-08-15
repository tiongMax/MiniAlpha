"""Command-line entry point for credential-free evaluation runs."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from evals.frozen import FrozenExecutor
from evals.loader import load_cases, load_frozen_outcomes
from evals.reporting import compare_summaries, write_results
from evals.runner import RunConfiguration, run_suite

PACKAGE_ROOT = Path(__file__).resolve().parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run MiniAlpha evaluations")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="run frozen credential-free cases")
    run.add_argument("--cases", type=Path, default=PACKAGE_ROOT / "cases" / "v1.json")
    run.add_argument(
        "--fixtures", type=Path, default=PACKAGE_ROOT / "fixtures" / "v1.json"
    )
    run.add_argument("--case", action="append", dest="case_ids")
    run.add_argument("--repeats", type=int, default=1)
    run.add_argument("--configuration", default="frozen-reference")
    run.add_argument("--output", type=Path, default=PACKAGE_ROOT / "results")

    compare = subparsers.add_parser("compare", help="compare two result JSON files")
    compare.add_argument("baseline", type=Path)
    compare.add_argument("candidate", type=Path)
    compare.add_argument(
        "--thresholds", type=Path, default=PACKAGE_ROOT / "thresholds.json"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        cases = load_cases(args.cases)
        if args.case_ids:
            selected = set(args.case_ids)
            cases = tuple(case for case in cases if case.case_id in selected)
            missing = selected.difference(case.case_id for case in cases)
            if missing:
                raise SystemExit(f"Unknown case IDs: {', '.join(sorted(missing))}")
        executor = FrozenExecutor(load_frozen_outcomes(args.fixtures))
        configuration = RunConfiguration(
            name=args.configuration,
            model=None,
            prompt_version="frozen-v1",
            graph_version="frozen-v1",
        )
        result = asyncio.run(
            run_suite(cases, executor, configuration, repeats=args.repeats)
        )
        json_path, markdown_path = write_results(
            args.output,
            configuration=configuration.name,
            trials=result.trials,
            grades=result.grades,
            summary=result.summary,
        )
        print(f"Wrote {json_path}")
        print(f"Wrote {markdown_path}")
        return 0 if all(grade.passed for grade in result.grades) else 1

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))["summary"]
    candidate = json.loads(args.candidate.read_text(encoding="utf-8"))["summary"]
    threshold_data = json.loads(args.thresholds.read_text(encoding="utf-8"))
    regressions = compare_summaries(
        baseline, candidate, threshold_data.get("maximum_regression", {})
    )
    for regression in regressions:
        print(regression)
    return 1 if regressions else 0


if __name__ == "__main__":
    raise SystemExit(main())
