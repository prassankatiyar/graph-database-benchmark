"""Charts for the README.

Four charts, each answering one question:
  1. traversal-latency.png  -- how does latency grow with hop depth?
  2. ingest-throughput.png  -- how fast can each platform be filled?
  3. concurrency-qps.png    -- does throughput scale with clients, or flatten?
  4. concurrency-p95.png    -- what does that scaling cost the tail?

Log scale on the latency axes, because a 3-hop query can be three orders of
magnitude slower than a point lookup and a linear axis would render four of
the five platforms as a flat line at zero.
"""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")  # no display on CI runners
import matplotlib.pyplot as plt  # noqa: E402

from . import config as cfg  # noqa: E402
from .runner import latest_results  # noqa: E402

HOPS = ("hop1", "hop2", "hop3")


def _platforms(results: dict) -> list[str]:
    return [k for k in cfg.REPORTED_PLATFORMS if k in results]


def chart_traversal(results: dict, out_dir) -> str:
    # Ten series (five platforms x p50/p95) will not fit inside the axes
    # without covering the very lines they label, so the legend goes outside
    # on the right and the figure is widened to pay for it.
    fig, ax = plt.subplots(figsize=(11, 5))
    x = range(1, 4)
    for key in _platforms(results):
        r = results[key]
        p50 = [r["reads"].get(h, {}).get("warm", {}).get("p50") for h in HOPS]
        p95 = [r["reads"].get(h, {}).get("warm", {}).get("p95") for h in HOPS]
        if any(v is None for v in p50):
            continue
        (line,) = ax.plot(x, p50, marker="o", label=f"{r['display_name']} p50")
        ax.plot(x, p95, marker="^", linestyle="--", color=line.get_color(), alpha=0.55,
                label=f"{r['display_name']} p95")
    ax.set_xticks(list(x))
    ax.set_xlabel("Traversal depth (hops)")
    ax.set_ylabel("Latency (ms, log scale)")
    ax.set_yscale("log")
    ax.set_title("Warm traversal latency by hop depth")
    ax.grid(True, which="both", alpha=0.25)
    ax.legend(fontsize=7, loc="center left", bbox_to_anchor=(1.01, 0.5),
              frameon=False)
    path = out_dir / "traversal-latency.png"
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return str(path)


def chart_ingest(results: dict, out_dir) -> str:
    keys = [k for k in _platforms(results) if results[k].get("ingest")]
    if not keys:
        return ""
    names = [results[k]["display_name"] for k in keys]
    rels = [results[k]["ingest"]["relationships_per_second"] for k in keys]
    nodes = [results[k]["ingest"]["nodes_per_second"] for k in keys]

    fig, ax = plt.subplots(figsize=(9, 5))
    positions = range(len(keys))
    width = 0.38
    ax.bar([p - width / 2 for p in positions], nodes, width, label="nodes/s")
    ax.bar([p + width / 2 for p in positions], rels, width, label="relationships/s")
    ax.set_xticks(list(positions))
    ax.set_xticklabels(names, rotation=20, ha="right", fontsize=8)
    ax.set_ylabel("Rows per second")
    ax.set_title("Ingest throughput (identical dataset, identical batch size)")
    ax.grid(True, axis="y", alpha=0.25)
    ax.legend()
    path = out_dir / "ingest-throughput.png"
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return str(path)


def _mixed_chart(results: dict, out_dir, field: str, ylabel: str, title: str, filename: str,
                 log: bool = False) -> str:
    fig, ax = plt.subplots(figsize=(9, 5))
    plotted = False
    for key in _platforms(results):
        runs = results[key].get("mixed", [])
        if not runs:
            continue
        xs = [r["concurrency"] for r in runs]
        ys = [
            r["qps"] if field == "qps" else r["read_latency"]["p95"]
            for r in runs
        ]
        ax.plot(xs, ys, marker="o", label=results[key]["display_name"])
        plotted = True
    if not plotted:
        plt.close(fig)
        return ""
    ax.set_xlabel("Concurrent clients")
    ax.set_ylabel(ylabel)
    if log:
        ax.set_yscale("log")
    ax.set_title(title)
    ax.grid(True, which="both", alpha=0.25)
    ax.legend(fontsize=8)
    path = out_dir / filename
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return str(path)


def build_all() -> list[str]:
    results = latest_results()
    results = {k: v for k, v in results.items() if k in cfg.REPORTED_PLATFORMS}
    if not results:
        raise SystemExit("No results to chart.")
    cfg.CHART_DIR.mkdir(parents=True, exist_ok=True)
    written = [
        chart_traversal(results, cfg.CHART_DIR),
        chart_ingest(results, cfg.CHART_DIR),
        _mixed_chart(
            results, cfg.CHART_DIR, "qps",
            "Sustained queries/second",
            "Mixed workload (90% read / 10% write): throughput vs client concurrency",
            "concurrency-qps.png",
        ),
        _mixed_chart(
            results, cfg.CHART_DIR, "p95",
            "Read p95 latency (ms, log scale)",
            "Mixed workload: tail latency vs client concurrency",
            "concurrency-p95.png",
            log=True,
        ),
    ]
    return [w for w in written if w]