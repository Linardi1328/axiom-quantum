from __future__ import annotations

import argparse
from pathlib import Path

from spy_market_agent.research.phase3b import run_phase3b
from spy_market_agent.research.runner import run_development_campaign


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m spy_market_agent.research.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser(
        "run-development",
        help="Run the offline Phase 3 development walk-forward research campaign.",
    )
    run_parser.add_argument("--manifest", required=True, type=Path)
    run_parser.add_argument("--data-root", required=True, type=Path)
    run_parser.add_argument(
        "--campaign-config",
        required=True,
        type=Path,
    )
    run_parser.add_argument(
        "--artifact-root",
        default=Path("artifacts/research"),
        type=Path,
    )
    phase3b_parser = subparsers.add_parser(
        "run-phase3b", help="Run the manual offline Phase 3B diagnostics on verified local data."
    )
    phase3b_parser.add_argument("--manifest", required=True, type=Path)
    phase3b_parser.add_argument("--data-root", required=True, type=Path)
    phase3b_parser.add_argument(
        "--config", default=Path("configs/research/phase3b_nonoverlapping.json"), type=Path
    )
    phase3b_parser.add_argument("--artifact-root", default=Path("artifacts/research"), type=Path)
    args = parser.parse_args(argv)
    if args.command == "run-development":
        result = run_development_campaign(
            manifest_path=args.manifest,
            data_root=args.data_root,
            campaign_config_path=args.campaign_config,
            artifact_root=args.artifact_root,
        )
        for line in result.summary_lines:
            print(line)
        return 0
    if args.command == "run-phase3b":
        phase3b_result = run_phase3b(
            manifest_path=args.manifest,
            data_root=args.data_root,
            config_path=args.config,
            artifact_root=args.artifact_root,
        )
        print(f"experiment_id={phase3b_result.experiment_id}")
        print(f"fold_count={phase3b_result.fold_count}")
        print("promotion_decision=NO CANDIDATE PROMOTION")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
