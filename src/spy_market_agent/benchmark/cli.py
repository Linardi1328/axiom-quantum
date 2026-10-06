from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path

from spy_market_agent.benchmark.dataset import record_feed_availability
from spy_market_agent.benchmark.errors import BenchmarkError
from spy_market_agent.benchmark.locks import BenchmarkRole
from spy_market_agent.benchmark.pipeline import (
    finalize_lock,
    prepare_benchmark,
    run_final_test,
    run_validation,
    validate_benchmark_lock,
)
from spy_market_agent.benchmark.verification import verify_benchmark_directory


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "record-feed-decision":
            return _record_feed_decision(args)
        if args.command == "prepare":
            return _prepare(args)
        if args.command == "validate":
            lock = validate_benchmark_lock(benchmark_lock_path=Path(args.benchmark_lock))
            print(f"benchmark_id={lock.benchmark_id}")
            return 0
        if args.command == "run-validation":
            validation = run_validation(benchmark_lock_path=Path(args.benchmark_lock))
            print(f"selected_model={validation.selected_model_name}")
            return 0
        if args.command == "finalize-lock":
            final_lock = finalize_lock(
                benchmark_lock_path=Path(args.benchmark_lock),
                acknowledge_final_test_policy=args.acknowledge_final_test_policy,
            )
            print(f"final_test_lock={final_lock.benchmark_id}")
            return 0
        if args.command == "run-final-test":
            final_result = run_final_test(
                final_test_lock_path=Path(args.final_test_lock),
                acknowledge_final_test_access=args.acknowledge_final_test_access,
                audit_replay=args.audit_replay,
            )
            print(f"benchmark_id={final_result['benchmark_id']}")
            return 0
        if args.command == "verify":
            verification = verify_benchmark_directory(
                Path(args.benchmark_root),
                require_runtime_reproduction=args.require_runtime_lineage,
            )
            print(f"benchmark_id={verification.benchmark_id}")
            print("verification=passed")
            return 0
    except BenchmarkError as exc:
        print(f"error={','.join(exc.codes)}")
        return 2
    parser.error("unknown command")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m spy_market_agent.benchmark.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    feed = subparsers.add_parser("record-feed-decision")
    feed.add_argument("--provider", required=True)
    feed.add_argument("--feed", required=True)
    feed.add_argument("--symbol", required=True)
    feed.add_argument("--timeframe", required=True)
    feed.add_argument("--adjustment", required=True)
    feed.add_argument("--start", required=True, type=_date)
    feed.add_argument("--end", required=True, type=_date)
    feed.add_argument("--probe-timestamp", type=_datetime, default=None)
    feed.add_argument("--available", action="store_true")
    feed.add_argument("--failure-category", default=None)
    feed.add_argument("--limitation", default=None)
    feed.add_argument("--evidence-source", required=True)
    feed.add_argument("--owner-acknowledge", action="store_true")
    feed.add_argument("--output", required=True)

    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--manifest", required=True)
    prepare.add_argument("--feed-record", required=True)
    prepare.add_argument(
        "--benchmark-role",
        choices=[role.value for role in BenchmarkRole],
        required=True,
    )
    prepare.add_argument("--latest-complete-research-year", required=True, type=int)
    prepare.add_argument("--artifact-root", default="./artifacts/benchmarks")
    prepare.add_argument("--owner-approve-assumptions", action="store_true")

    validate = subparsers.add_parser("validate")
    validate.add_argument("--benchmark-lock", required=True)

    run_validation_parser = subparsers.add_parser("run-validation")
    run_validation_parser.add_argument("--benchmark-lock", required=True)

    finalize = subparsers.add_parser("finalize-lock")
    finalize.add_argument("--benchmark-lock", required=True)
    finalize.add_argument("--acknowledge-final-test-policy", action="store_true")

    final = subparsers.add_parser("run-final-test")
    final.add_argument("--final-test-lock", required=True)
    final.add_argument("--acknowledge-final-test-access", action="store_true")
    final.add_argument("--audit-replay", action="store_true")

    verify = subparsers.add_parser("verify")
    verify.add_argument("--benchmark-root", required=True)
    verify.add_argument("--require-runtime-lineage", action="store_true")
    return parser


def _record_feed_decision(args: argparse.Namespace) -> int:
    timestamp = args.probe_timestamp or datetime.now(tz=UTC)
    record = record_feed_availability(
        provider=args.provider,
        requested_feed=args.feed,
        symbol=args.symbol,
        timeframe=args.timeframe,
        adjustment_mode=args.adjustment,
        requested_start=args.start,
        requested_end=args.end,
        probe_timestamp=timestamp,
        success=args.available,
        owner_acknowledgement=args.owner_acknowledge,
        evidence_source_description=args.evidence_source,
        output=Path(args.output),
        entitlement_or_subscription_limitation=args.limitation,
        sanitized_failure_category=args.failure_category,
    )
    print(f"provider={record.provider}")
    print(f"feed={record.requested_feed}")
    print(f"available={str(record.success).lower()}")
    return 0


def _prepare(args: argparse.Namespace) -> int:
    lock = prepare_benchmark(
        manifest_path=Path(args.manifest),
        feed_record_path=Path(args.feed_record),
        benchmark_role=BenchmarkRole(args.benchmark_role),
        latest_complete_research_year=args.latest_complete_research_year,
        artifact_root=Path(args.artifact_root),
        owner_approve_assumptions=args.owner_approve_assumptions,
    )
    print(f"benchmark_id={lock.benchmark_id}")
    print(f"benchmark_lock={args.artifact_root}/{lock.benchmark_id}/benchmark_lock.json")
    return 0


def _date(value: str) -> date:
    return date.fromisoformat(value)


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        msg = "timestamp must be timezone-aware"
        raise argparse.ArgumentTypeError(msg)
    return parsed.astimezone(UTC)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
