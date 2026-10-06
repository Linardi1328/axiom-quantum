from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from spy_market_agent.config import Settings
from spy_market_agent.market_data.acquisition import (
    acquisition_report_lines,
    require_market_data_credentials,
    utc_now,
    validate_request_without_client_construction,
)
from spy_market_agent.market_data.errors import MarketDataAcquisitionError, redact_secret_text
from spy_market_agent.market_data.pipeline import acquire_historical_spy_data
from spy_market_agent.market_data.storage import DatasetStore


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "acquire":
        return _run_acquire(args)
    if args.command == "verify":
        return _run_verify(args)
    parser.print_help()
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m spy_market_agent.market_data.cli",
        description="Explicit Phase 1 historical SPY market-data commands.",
    )
    subparsers = parser.add_subparsers(dest="command")
    acquire = subparsers.add_parser(
        "acquire",
        help="Acquire historical SPY daily bars from the approved provider.",
    )
    acquire.add_argument("--provider", required=True)
    acquire.add_argument("--symbol", required=True)
    acquire.add_argument("--start", required=True, type=_parse_date)
    acquire.add_argument("--end", required=True, type=_parse_date)
    acquire.add_argument("--timeframe", required=True)
    acquire.add_argument("--feed", default=os.environ.get("ALPACA_MARKET_DATA_FEED", "sip"))
    acquire.add_argument("--adjustment", required=True)
    acquire.add_argument("--data-root", default=os.environ.get("MARKET_DATA_ROOT", "./data"))
    acquire.add_argument("--asof", type=_parse_date, default=None)
    acquire.add_argument(
        "--acknowledge-provider-terms",
        action="store_true",
        help="Confirm that provider terms have been reviewed for local acquisition.",
    )
    verify = subparsers.add_parser(
        "verify",
        help="Verify existing Phase 1 raw, canonical, and manifest checksums.",
    )
    verify.add_argument("--manifest", required=True)
    verify.add_argument("--data-root", default=os.environ.get("MARKET_DATA_ROOT", "./data"))
    return parser


def _run_acquire(args: argparse.Namespace) -> int:
    try:
        request = validate_request_without_client_construction(
            symbol=args.symbol,
            start_date=args.start,
            end_date=args.end,
            timeframe=args.timeframe,
            provider=args.provider,
            feed=args.feed,
            adjustment_mode=args.adjustment,
            data_root=Path(args.data_root),
            acknowledge_provider_terms=args.acknowledge_provider_terms,
            asof=args.asof,
        )
        credentials = require_market_data_credentials()
        settings = Settings()

        from spy_market_agent.market_data.alpaca_provider import AlpacaMarketDataProvider

        provider = AlpacaMarketDataProvider(
            max_retries=settings.market_data_max_retries,
            timeout_seconds=settings.market_data_timeout_seconds,
        )
        artifacts = acquire_historical_spy_data(
            request,
            provider=provider,
            credentials=credentials,
            clock=utc_now,
        )
    except (MarketDataAcquisitionError, ValueError) as exc:
        print(f"acquisition failed: {redact_secret_text(exc)}", file=sys.stderr)
        return 1

    for line in acquisition_report_lines(artifacts):
        print(line)
    return 0


def _run_verify(args: argparse.Namespace) -> int:
    try:
        store = DatasetStore(Path(args.data_root))
        manifest = store.verify_manifest_artifacts(Path(args.manifest))
    except (MarketDataAcquisitionError, ValueError) as exc:
        print(f"verification failed: {redact_secret_text(exc)}", file=sys.stderr)
        return 1

    print(f"dataset_id={manifest.dataset_id}")
    print(f"canonical_checksum={manifest.canonical_content_checksum}")
    print("verification=passed")
    return 0


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must be an unambiguous ISO date.") from exc


if __name__ == "__main__":
    raise SystemExit(main())
