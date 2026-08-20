"""Print a one-screen summary of the most recent result for one platform.

Useful for sanity-checking a short probe run before committing to a full
sweep: if the median rows returned by a 3-hop traversal is a large fraction of
the graph, the workload is heavier than intended and a network-bound platform
will take hours rather than minutes.

    python scripts/peek.py memgraph
    python scripts/peek.py            # every platform with a result on disk
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "results" / "raw"


def latest_for(platform: str) -> Path | None:
    files = sorted(RAW.glob(f"{platform}_*.json"))
    return files[-1] if files else None


def summarise(path: Path) -> None:
    data = json.loads(path.read_text())
    print()
    print(f"=== {data['display_name']} ===")
    print(f"  file        {path.name}")
    print(f"  dataset     {data['dataset']['node_count']:,} nodes / "
          f"{data['dataset']['edge_count']:,} rels")

    rtt = data.get("network", {}).get("tcp_rtt", {}).get("median_ms")
    if rtt is not None:
        print(f"  tcp rtt     {rtt} ms  <- floor under every latency below")

    ingest = data.get("ingest")
    if ingest:
        print(f"  ingest      {ingest['nodes_per_second']:,.0f} nodes/s, "
              f"{ingest['relationships_per_second']:,.0f} rels/s, "
              f"{ingest['total_seconds']:.1f} s total")

    reads = data.get("reads", {})
    if reads:
        print()
        print(f"  {'workload':<18}{'p50 (ms)':>12}{'p95 (ms)':>12}"
              f"{'rows':>10}{'errors':>9}")
        for name, block in reads.items():
            warm = block.get("warm", {})
            print(f"  {name:<18}{warm.get('p50', float('nan')):>12.2f}"
                  f"{warm.get('p95', float('nan')):>12.2f}"
                  f"{warm.get('rows_returned_median', 0):>10.0f}"
                  f"{warm.get('errors', 0):>9}")

    mixed = data.get("mixed", [])
    if mixed:
        print()
        print(f"  {'clients':<18}{'qps':>12}{'read p95':>12}{'errors':>10}")
        for run in mixed:
            print(f"  {run['concurrency']:<18}{run['qps']:>12.1f}"
                  f"{run['read_latency']['p95']:>12.2f}{run['errors']:>10}")

    caveats = data.get("caveats", [])
    if caveats:
        print()
        for c in caveats:
            print(f"  caveat: {c}")


def main() -> int:
    if not RAW.exists():
        print("No results directory yet.")
        return 1

    if len(sys.argv) > 1:
        targets = sys.argv[1:]
    else:
        targets = sorted({p.name.rsplit("_", 1)[0] for p in RAW.glob("*.json")})

    if not targets:
        print("No result files in results/raw/ yet.")
        return 1

    for platform in targets:
        path = latest_for(platform)
        if path is None:
            print(f"\nNo results found for '{platform}'.")
            continue
        summarise(path)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())