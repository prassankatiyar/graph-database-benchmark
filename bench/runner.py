"""Orchestration: what runs, in what order, and what gets written to disk.

The output of a run is one JSON file per platform under results/raw/. It is
self-describing: it carries the dataset hash, the harness version, the host
fingerprint, the exact index DDL that was applied, the measured RTT to the
endpoint, and every caveat the run produced. A results file you cannot audit
six months later is not a result.
"""

from __future__ import annotations

import json
import platform as pyplatform
import random
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from . import __version__, config as cfg, dataset
from .adapters import build
from .adapters.base import Adapter
from .metrics import RepeatVariance
from .workloads import Series, measure, measure_nullary, run_mixed

READ_WORKLOADS = ("hop1", "hop2", "hop3", "point_lookup", "filtered_lookup", "aggregation")


# --------------------------------------------------------------------------
# Shared, seed-derived inputs -- drawn once, replayed against every platform
# --------------------------------------------------------------------------


def shared_inputs() -> dict:
    """Start nodes and filter values, cached on disk.

    Cached on purpose. If platform A is benchmarked today and platform B
    tomorrow, they must still be asked about the same nodes, or the comparison
    is worthless.
    """
    path = cfg.RESULTS_DIR / "shared_inputs.json"
    manifest = dataset.load_manifest()

    if path.exists():
        cached = json.loads(path.read_text())
        # The pool is only valid for the dataset it was drawn from. After a
        # rebuild (or after the smoke-test fixture) the cached node ids may not
        # exist in the graph at all, and every traversal would quietly return
        # zero rows very fast -- which looks like a great benchmark result.
        if cached.get("edges_csv_sha256") == manifest.edges_csv_sha256:
            return cached
        print("  inputs  dataset changed since last run — redrawing start nodes")

    nodes = dataset.read_nodes()
    edges = dataset.read_edges()

    # Only draw start nodes that actually have outgoing edges; a start node
    # with zero out-degree makes hop1/2/3 trivially empty and would quietly
    # deflate every platform's traversal numbers by the same amount, hiding
    # real differences in the tail.
    with_out_edges = sorted({e["src"] for e in edges})
    rng = random.Random(cfg.SEED)
    start_nodes = rng.sample(
        with_out_edges, min(cfg.START_NODE_POOL, len(with_out_edges))
    )

    all_ids = [n["id"] for n in nodes]
    point_ids = rng.sample(all_ids, min(cfg.START_NODE_POOL, len(all_ids)))
    years = sorted({n["year"] for n in nodes})

    payload = {
        "seed": cfg.SEED,
        "dataset_name": manifest.name,
        "edges_csv_sha256": manifest.edges_csv_sha256,
        "start_nodes": start_nodes,
        "point_ids": point_ids,
        "filter_years": years,
        "filter_limit": 100,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))
    return payload


def host_fingerprint() -> dict:
    """Enough about the client machine to spot 'ran it from a different box'."""
    try:
        git_sha = (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=cfg.ROOT,
                stderr=subprocess.DEVNULL,
            )
            .decode()
            .strip()
        )
    except Exception:  # noqa: BLE001
        git_sha = "unknown"
    return {
        "hostname": socket.gethostname(),
        "python": pyplatform.python_version(),
        "platform": pyplatform.platform(),
        "processor": pyplatform.processor() or "unknown",
        "harness_version": __version__,
        "git_sha": git_sha,
    }


# --------------------------------------------------------------------------
# Phases
# --------------------------------------------------------------------------


def do_load(adapter: Adapter, *, reset: bool = True) -> dict:
    nodes = dataset.read_nodes()
    edges = dataset.read_edges()
    manifest = dataset.load_manifest()

    if reset:
        print("  reset   clearing existing data")
        adapter.reset()

    print("  schema  applying indexes/constraints")
    ddl = adapter.create_schema()

    print(f"  load    {len(nodes):,} nodes / {len(edges):,} relationships")
    result = adapter.load(nodes, edges, cfg.BATCH_SIZE)

    node_count, rel_count = adapter.count_graph()
    matches = node_count == manifest.node_count and rel_count == manifest.edge_count
    print(
        f"  verify  db reports {node_count:,} nodes / {rel_count:,} rels "
        f"({'match' if matches else 'MISMATCH'})"
    )
    if not matches:
        print(
            "          !! the graph in the database does not match the dataset. "
            "Latency numbers from this instance are not comparable."
        )

    return {
        "ingest": result.to_dict(),
        "schema_applied": ddl,
        "verified_node_count": node_count,
        "verified_relationship_count": rel_count,
        "dataset_matches": matches,
    }


def do_reads(adapter: Adapter, inputs: dict, repeats: int = cfg.REPEATS) -> dict:
    """Run the read suite `repeats` times and report per-repeat variance."""
    per_repeat: dict[str, list[Series]] = {name: [] for name in READ_WORKLOADS}

    for repeat in range(repeats):
        print(f"  reads   repeat {repeat + 1}/{repeats}")
        per_repeat["hop1"].append(
            measure("hop1", lambda n: adapter.q_hop(n, 1), inputs["start_nodes"])
        )
        per_repeat["hop2"].append(
            measure("hop2", lambda n: adapter.q_hop(n, 2), inputs["start_nodes"])
        )
        per_repeat["hop3"].append(
            measure("hop3", lambda n: adapter.q_hop(n, 3), inputs["start_nodes"])
        )
        per_repeat["point_lookup"].append(
            measure("point_lookup", adapter.q_point_lookup, inputs["point_ids"])
        )
        per_repeat["filtered_lookup"].append(
            measure(
                "filtered_lookup",
                lambda y: adapter.q_filtered_lookup(y, inputs["filter_limit"]),
                inputs["filter_years"],
            )
        )
        per_repeat["aggregation"].append(
            measure_nullary("aggregation", adapter.q_aggregation)
        )

    out: dict = {}
    for name, series_list in per_repeat.items():
        # The headline numbers come from repeat 1 pooled with the rest: we
        # concatenate the warm samples so the reported percentiles are over
        # repeats * ITERATIONS observations.
        pooled_warm = [ms for s in series_list for ms in s.warm_ms]
        pooled_cold = [ms for s in series_list for ms in s.cold_ms]
        pooled_rows = [r for s in series_list for r in s.rows]
        pooled = Series(
            name=name,
            warm_ms=pooled_warm,
            cold_ms=pooled_cold,
            rows=pooled_rows,
            errors=sum(s.errors for s in series_list),
            timeouts=sum(s.timeouts for s in series_list),
            error_examples=[e for s in series_list for e in s.error_examples][:5],
        )
        p50_across = RepeatVariance([s.summary().p50 for s in series_list])
        p95_across = RepeatVariance([s.summary().p95 for s in series_list])
        out[name] = {
            "warm": pooled.summary().to_dict(),
            "cold": pooled.cold_summary().to_dict(),
            "variance_p50_across_repeats": p50_across.to_dict(),
            "variance_p95_across_repeats": p95_across.to_dict(),
            "error_examples": pooled.error_examples,
        }
    return out


def do_mixed(adapter: Adapter, inputs: dict) -> list[dict]:
    results = []
    for level in cfg.CONCURRENCY_LEVELS:
        print(f"  mixed   {level} client(s) for {cfg.MIXED_DURATION_S:.0f}s")
        summary = run_mixed(adapter, inputs["start_nodes"], level)
        print(
            f"          {summary.qps:,.1f} qps "
            f"(read p95 {summary.read_latency.p95} ms, errors {summary.errors})"
        )
        results.append(summary.to_dict())
    return results


# --------------------------------------------------------------------------
# Entry point for a full platform run
# --------------------------------------------------------------------------


def run_platform(
    platform_key: str,
    *,
    do_load_phase: bool = True,
    do_read_phase: bool = True,
    do_mixed_phase: bool = True,
    repeats: int = cfg.REPEATS,
) -> Path:
    spec = cfg.PLATFORMS[platform_key]
    manifest = dataset.load_manifest()
    inputs = shared_inputs()

    print(f"\n=== {spec.display_name} ===")
    record: dict = {
        "platform": platform_key,
        "display_name": spec.display_name,
        "engine": spec.engine,
        "query_language": spec.query_language,
        "deployment": spec.deployment,
        "advertised": {
            "vcpu": spec.advertised_vcpu,
            "ram": spec.advertised_ram,
            "disk": spec.advertised_disk,
        },
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "host": host_fingerprint(),
        "dataset": {
            "name": manifest.name,
            "node_count": manifest.node_count,
            "edge_count": manifest.edge_count,
            "nodes_csv_sha256": manifest.nodes_csv_sha256,
            "edges_csv_sha256": manifest.edges_csv_sha256,
        },
        "config": {
            "iterations": cfg.ITERATIONS,
            "warmup": cfg.WARMUP,
            "repeats": repeats,
            "batch_size": cfg.BATCH_SIZE,
            "concurrency_levels": list(cfg.CONCURRENCY_LEVELS),
            "mixed_duration_s": cfg.MIXED_DURATION_S,
            "read_ratio": cfg.READ_RATIO,
        },
        "caveats": [],
    }

    adapter = build(platform_key)
    t0 = time.perf_counter()
    with adapter:
        print("  probe   TCP round trip to endpoint")
        record["network"] = {"tcp_rtt": adapter.tcp_rtt_ms()}

        if do_load_phase:
            record.update(do_load(adapter))
            if not record.get("dataset_matches", True):
                record["caveats"].append(
                    "Loaded graph size does not match the dataset manifest."
                )
        else:
            node_count, rel_count = adapter.count_graph()
            record["verified_node_count"] = node_count
            record["verified_relationship_count"] = rel_count
            record["caveats"].append("Load phase skipped; reusing existing data.")

        if do_read_phase:
            record["reads"] = do_reads(adapter, inputs, repeats)

        if do_mixed_phase:
            record["mixed"] = do_mixed(adapter, inputs)

        print("  probe   footprint")
        record["footprint"] = adapter.footprint()

    record["total_seconds"] = round(time.perf_counter() - t0, 2)
    record["finished_utc"] = datetime.now(timezone.utc).isoformat()

    cfg.RAW_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = cfg.RAW_DIR / f"{platform_key}_{stamp}.json"
    out_path.write_text(json.dumps(record, indent=2, default=str))
    print(f"  wrote   {out_path.relative_to(cfg.ROOT)}")
    return out_path


def latest_results() -> dict[str, dict]:
    """Most recent result file per platform."""
    latest: dict[str, tuple[float, dict]] = {}
    for path in sorted(cfg.RAW_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        key = data.get("platform")
        if not key:
            continue
        mtime = path.stat().st_mtime
        if key not in latest or mtime > latest[key][0]:
            latest[key] = (mtime, data)
    return {k: v[1] for k, v in latest.items()}