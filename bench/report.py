"""Turn results/raw/*.json into the results matrix in RESULTS.md.

The README embeds this file rather than duplicating it, so there is exactly
one place where a number can be wrong. Nothing here computes a statistic --
report.py only formats what runner.py measured. If you want to change what is
reported, change the measurement, not the formatter.
"""

from __future__ import annotations

from datetime import datetime, timezone

from tabulate import tabulate

from . import config as cfg
from .runner import latest_results

HOP_LABELS = {"hop1": "1-hop", "hop2": "2-hop", "hop3": "3-hop"}
LOOKUP_LABELS = {
    "point_lookup": "Point lookup",
    "filtered_lookup": "Filtered lookup (indexed year)",
    "aggregation": "Aggregation (count group-by year)",
}


def _fmt(value, digits: int = 2, suffix: str = "") -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float) and value != value:  # NaN
        return "n/a"
    if isinstance(value, (int, float)):
        return f"{value:,.{digits}f}{suffix}"
    return str(value)


def _order(results: dict) -> list[str]:
    """CognoDB first (it is the subject), then the rest in config order."""
    keys = [k for k in cfg.REPORTED_PLATFORMS if k in results]
    return keys


def table_environment(results: dict) -> str:
    rows = []
    for key in _order(results):
        r = results[key]
        adv = r.get("advertised", {})
        rtt = r.get("network", {}).get("tcp_rtt", {}).get("median_ms")
        rows.append(
            [
                r["display_name"],
                r["engine"],
                r["query_language"],
                r["deployment"],
                adv.get("vcpu", "?"),
                adv.get("ram", "?"),
                adv.get("disk", "?"),
                _fmt(rtt, 2, " ms"),
            ]
        )
    return tabulate(
        rows,
        headers=[
            "Platform",
            "Engine",
            "Query language",
            "Deployment",
            "vCPU",
            "RAM",
            "Disk",
            "TCP RTT (median)",
        ],
        tablefmt="github",
    )


def table_ingest(results: dict) -> str:
    rows = []
    for key in _order(results):
        r = results[key]
        ing = r.get("ingest")
        if not ing:
            rows.append([r["display_name"], "not run", "", "", "", ""])
            continue
        rows.append(
            [
                r["display_name"],
                _fmt(ing["nodes_per_second"], 0),
                _fmt(ing["relationships_per_second"], 0),
                _fmt(ing["total_seconds"], 1, " s"),
                f"{r.get('verified_node_count', '?'):,} / "
                f"{r.get('verified_relationship_count', '?'):,}",
                ing["method"],
            ]
        )
    return tabulate(
        rows,
        headers=[
            "Platform",
            "Nodes/s",
            "Rels/s",
            "Total load time",
            "Verified nodes / rels",
            "Load method",
        ],
        tablefmt="github",
    )


def table_reads(results: dict, workloads: dict[str, str], series: str = "warm") -> str:
    rows = []
    for key in _order(results):
        r = results[key]
        reads = r.get("reads", {})
        row = [r["display_name"]]
        for wl in workloads:
            block = reads.get(wl, {}).get(series, {})
            row.append(_fmt(block.get("p50"), 2))
            row.append(_fmt(block.get("p95"), 2))
        rows.append(row)
    headers = ["Platform"]
    for label in workloads.values():
        headers += [f"{label} p50 (ms)", f"{label} p95 (ms)"]
    return tabulate(rows, headers=headers, tablefmt="github")


def table_rows_returned(results: dict) -> str:
    """Proof that every platform did the same amount of work.

    If one database returns 12 rows for a 3-hop query and another returns
    1,180, their latencies are not comparable and no amount of percentile
    hygiene fixes that.
    """
    rows = []
    for key in _order(results):
        r = results[key]
        reads = r.get("reads", {})
        rows.append(
            [
                r["display_name"],
                *[
                    _fmt(reads.get(wl, {}).get("warm", {}).get("rows_returned_median"), 1)
                    for wl in ("hop1", "hop2", "hop3", "filtered_lookup", "aggregation")
                ],
            ]
        )
    return tabulate(
        rows,
        headers=["Platform", "1-hop", "2-hop", "3-hop", "Filtered", "Aggregation"],
        tablefmt="github",
    )


def table_variance(results: dict) -> str:
    rows = []
    for key in _order(results):
        r = results[key]
        reads = r.get("reads", {})
        row = [r["display_name"]]
        for wl in ("hop1", "hop2", "hop3", "point_lookup"):
            cv = reads.get(wl, {}).get("variance_p50_across_repeats", {}).get("cv_percent")
            row.append(_fmt(cv, 2, "%"))
        rows.append(row)
    return tabulate(
        rows,
        headers=[
            "Platform",
            "1-hop p50 CV",
            "2-hop p50 CV",
            "3-hop p50 CV",
            "Point p50 CV",
        ],
        tablefmt="github",
    )


def table_mixed(results: dict) -> str:
    rows = []
    for key in _order(results):
        r = results[key]
        for run in r.get("mixed", []):
            rows.append(
                [
                    r["display_name"],
                    run["concurrency"],
                    _fmt(run["qps"], 1),
                    _fmt(run["read_latency"]["p50"], 2),
                    _fmt(run["read_latency"]["p95"], 2),
                    _fmt(run["write_latency"]["p50"], 2),
                    _fmt(run["write_latency"]["p95"], 2),
                    run["errors"],
                ]
            )
    return tabulate(
        rows,
        headers=[
            "Platform",
            "Clients",
            "Sustained QPS",
            "Read p50 (ms)",
            "Read p95 (ms)",
            "Write p50 (ms)",
            "Write p95 (ms)",
            "Errors",
        ],
        tablefmt="github",
    )


def table_indexes(results: dict) -> str:
    """Which properties are indexed on each platform.

    Generated from `schema_applied`, i.e. the statements the adapter actually
    executed against the live instance -- not from what the README author
    believed was applied. Section 5.2 of the brief asks for this explicitly,
    and it is also the only way a reader can tell whether a fast lookup was
    fast because of the engine or because of an index the others did not get.
    """
    rows = []
    for key in _order(results):
        r = results[key]
        applied = r.get("schema_applied") or ["not recorded (load phase skipped)"]
        rows.append([r["display_name"], "<br>".join(f"`{s}`" for s in applied)])
    return tabulate(
        rows, headers=["Platform", "Index / constraint DDL executed"], tablefmt="github"
    )


def table_footprint(results: dict) -> str:
    rows = []
    for key in _order(results):
        r = results[key]
        fp = r.get("footprint", {})
        observable = {
            k: v
            for k, v in fp.items()
            if isinstance(v, (str, int, float)) and "not observable" not in str(v)
        }
        rows.append(
            [
                r["display_name"],
                "; ".join(f"{k}={v}" for k, v in observable.items()) or "not observable",
                "; ".join(
                    k for k, v in fp.items() if "not observable" in str(v)
                )
                or "-",
            ]
        )
    return tabulate(
        rows,
        headers=["Platform", "Observable footprint", "Not observable"],
        tablefmt="github",
    )


def section_caveats(results: dict) -> str:
    lines = []
    for key in _order(results):
        r = results[key]
        caveats = list(r.get("caveats", []))
        for wl, block in r.get("reads", {}).items():
            warm = block.get("warm", {})
            if warm.get("errors"):
                caveats.append(
                    f"{wl}: {warm['errors']} failed queries "
                    f"(examples: {'; '.join(block.get('error_examples', [])) or 'n/a'})"
                )
            if warm.get("timeouts"):
                caveats.append(f"{wl}: {warm['timeouts']} queries over the timeout")
        for run in r.get("mixed", []):
            if run.get("errors"):
                caveats.append(
                    f"mixed @ {run['concurrency']} clients: {run['errors']} errors"
                )
        if not caveats:
            caveats = ["No errors, timeouts or load mismatches recorded."]
        lines.append(f"**{r['display_name']}**")
        lines.extend(f"- {c}" for c in caveats)
        lines.append("")
    return "\n".join(lines)


def build(results: dict | None = None) -> str:
    results = results if results is not None else latest_results()
    results = {k: v for k, v in results.items() if k in cfg.REPORTED_PLATFORMS}
    if not results:
        raise SystemExit(
            "No results found in results/raw/. Run `python -m bench run --platform ...` first."
        )

    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    synthetic = [
        r["display_name"]
        for r in results.values()
        if r.get("dataset", {}).get("name") == "synthetic-fixture"
    ]
    banner = []
    if synthetic:
        banner = [
            "> **These results are not publishable.** The following platforms were",
            "> measured against the synthetic smoke-test fixture rather than the real",
            f"> dataset: {', '.join(synthetic)}. Re-run `python -m bench dataset`",
            "> without `--fixture` and benchmark again.",
            "",
        ]

    hashes = {r.get("dataset", {}).get("edges_csv_sha256") for r in results.values()}
    if len(hashes) > 1:
        banner += [
            "> **Dataset mismatch.** Not every platform was measured against the same",
            "> edge file (differing sha256). The cross-platform comparison below is",
            "> invalid until every platform is reloaded from one dataset build.",
            "",
        ]

    parts = [
        "# Results matrix",
        "",
        *banner,
        f"_Generated {generated} by `python -m bench report`. "
        "Do not hand-edit: regenerate it._",
        "",
        "## 1. Environment and tier parity",
        "",
        table_environment(results),
        "",
        "> `TCP RTT (median)` is the floor under every latency below. Self-hosted",
        "> instances have a near-zero RTT and managed instances do not, so subtract",
        "> it before comparing a managed platform against a local container.",
        "",
        "## 2. Ingest throughput",
        "",
        table_ingest(results),
        "",
        "## 3. Traversal latency (warm)",
        "",
        table_reads(results, HOP_LABELS, "warm"),
        "",
        "## 4. Traversal latency (cold, first 30 iterations)",
        "",
        table_reads(results, HOP_LABELS, "cold"),
        "",
        "## 5. Lookups and aggregation (warm)",
        "",
        table_reads(results, LOOKUP_LABELS, "warm"),
        "",
        "### Indexes in place during these measurements",
        "",
        table_indexes(results),
        "",
        "## 6. Result-set parity check (median rows returned)",
        "",
        table_rows_returned(results),
        "",
        "## 7. Run-to-run variance (coefficient of variation of p50 across repeats)",
        "",
        table_variance(results),
        "",
        "## 8. Mixed workload — concurrency sweep",
        "",
        table_mixed(results),
        "",
        "## 9. Footprint",
        "",
        table_footprint(results),
        "",
        "## 10. Caveats recorded by the harness",
        "",
        section_caveats(results),
    ]
    return "\n".join(parts) + "\n"


BEGIN_MARKER = "<!-- BEGIN RESULTS -->"
END_MARKER = "<!-- END RESULTS -->"


def write(path=None) -> str:
    path = path or (cfg.ROOT / "RESULTS.md")
    content = build()
    # encoding is explicit because Path.write_text() defaults to the platform
    # locale, which on Windows is cp1252. That silently mangles every em dash
    # and arrow in the report into a replacement character.
    path.write_text(content, encoding="utf-8")
    embed_into_readme(content)
    return str(path)


def embed_into_readme(content: str, readme=None) -> bool:
    """Splice the generated matrix into README.md between the HTML markers.

    Copy-pasting results into a README by hand is how a README ends up
    disagreeing with the data it is supposedly reporting. If the markers are
    absent the README is left alone.
    """
    readme = readme or (cfg.ROOT / "README.md")
    if not readme.exists():
        return False
    text = readme.read_text(encoding="utf-8")
    if BEGIN_MARKER not in text or END_MARKER not in text:
        return False
    head, _, rest = text.partition(BEGIN_MARKER)
    _, _, tail = rest.partition(END_MARKER)
    # Strip the generated file's own H1 so heading levels stay sane in README.
    body = "\n".join(content.splitlines()[1:]).lstrip("\n")
    readme.write_text(
        f"{head}{BEGIN_MARKER}\n\n{body}\n{END_MARKER}{tail}", encoding="utf-8"
    )
    return True