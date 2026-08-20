"""Command line interface.

    python -m bench dataset            # download + sample + freeze the dataset
    python -m bench doctor             # connectivity + RTT for every platform
    python -m bench run --platform X   # load + read suite + mixed sweep
    python -m bench report             # regenerate RESULTS.md and charts
    python -m bench selftest           # full pipeline against the mock backend
"""

from __future__ import annotations

import argparse
import sys
import traceback

from . import charts, config as cfg, dataset, report
from .adapters import available, build
from .runner import run_platform, shared_inputs


def cmd_dataset(args) -> int:
    if args.fixture:
        manifest = dataset.make_fixture()
    else:
        manifest = dataset.prepare(force=args.force)
    print()
    print(f"  dataset : {manifest.name}")
    print(f"  nodes   : {manifest.node_count:,}")
    print(f"  rels    : {manifest.edge_count:,}")
    print(f"  years   : {manifest.distinct_years}")
    print(f"  sha256  : nodes={manifest.nodes_csv_sha256[:16]}… "
          f"edges={manifest.edges_csv_sha256[:16]}…")
    shared_inputs()
    print("  inputs  : results/shared_inputs.json (start nodes frozen)")
    return 0


def cmd_doctor(args) -> int:
    targets = args.platform or [k for k in cfg.PLATFORMS if k != "mock"]
    failures = 0
    for key in targets:
        spec = cfg.PLATFORMS[key]
        print(f"\n--- {spec.display_name} ---")
        try:
            adapter = build(key)
        except RuntimeError as exc:
            print(f"  config  FAIL  {exc}")
            failures += 1
            continue
        try:
            with adapter:
                rtt = adapter.tcp_rtt_ms(samples=10)
                nodes, rels = adapter.count_graph()
                print(f"  connect OK")
                print(f"  rtt     median {rtt.get('median_ms')} ms")
                print(f"  data    {nodes:,} nodes / {rels:,} relationships")
        except Exception as exc:  # noqa: BLE001
            print(f"  connect FAIL  {type(exc).__name__}: {exc}")
            if args.verbose:
                traceback.print_exc()
            failures += 1
    print(f"\n{len(targets) - failures}/{len(targets)} platforms reachable.")
    return 1 if failures else 0


def cmd_run(args) -> int:
    targets = args.platform or [k for k in cfg.PLATFORMS if k != "mock"]
    failed = []
    for key in targets:
        try:
            run_platform(
                key,
                do_load_phase=not args.skip_load,
                do_read_phase=not args.skip_reads,
                do_mixed_phase=not args.skip_mixed,
                repeats=args.repeats,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  !! {key} failed: {type(exc).__name__}: {exc}")
            if args.verbose:
                traceback.print_exc()
            failed.append(key)
    if failed:
        print(f"\nFailed platforms: {', '.join(failed)}")
        return 1
    return 0


def cmd_report(args) -> int:
    path = report.write()
    print(f"  wrote   {path}")
    if not args.no_charts:
        try:
            for chart in charts.build_all():
                print(f"  wrote   {chart}")
        except Exception as exc:  # noqa: BLE001
            print(f"  charts skipped: {type(exc).__name__}: {exc}")
    return 0


def cmd_selftest(args) -> int:
    """Exercise every code path with the mock backend and tiny parameters."""
    cfg.ITERATIONS = 30
    cfg.WARMUP = 5
    cfg.MIXED_DURATION_S = 2.0
    cfg.CONCURRENCY_LEVELS = (1, 4)
    print("Running the full pipeline against the mock backend…")
    run_platform("mock", repeats=2)
    print("\nSelf-test complete. Results written to results/raw/mock_*.json")
    print("(The mock backend is not a database and never appears in RESULTS.md.)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bench", description="Graph database cloud benchmark harness"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("dataset", help="download, sample and freeze the dataset")
    p.add_argument("--force", action="store_true", help="rebuild even if cached")
    p.add_argument(
        "--fixture",
        action="store_true",
        help="generate a small synthetic graph offline (smoke tests only, "
        "never for published results)",
    )
    p.set_defaults(func=cmd_dataset)

    p = sub.add_parser("doctor", help="check connectivity to every platform")
    p.add_argument("--platform", action="append", choices=available())
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("run", help="run the benchmark")
    p.add_argument("--platform", action="append", choices=available())
    p.add_argument("--skip-load", action="store_true", help="reuse loaded data")
    p.add_argument("--skip-reads", action="store_true")
    p.add_argument("--skip-mixed", action="store_true")
    p.add_argument("--repeats", type=int, default=cfg.REPEATS)
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("report", help="regenerate RESULTS.md and charts")
    p.add_argument("--no-charts", action="store_true")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("selftest", help="run the pipeline against the mock backend")
    p.set_defaults(func=cmd_selftest)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
