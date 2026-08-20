"""Fetch, sample and freeze the benchmark dataset.

The output of this module is two CSV files plus a manifest containing the
sha256 of each. Every platform loads those exact bytes, and the manifest hash
is written into every result file, so a result can always be traced back to the
data it was produced from.

Why snowball sampling and not a random edge sample: a uniformly random subset
of edges shreds the local clustering of the graph, which makes 2- and 3-hop
traversals fan out far less than they would in the real network. That would
flatter every database equally, but it would also make the traversal numbers
meaningless. Snowballing from a fixed seed node keeps a dense, connected,
realistic neighbourhood structure.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import random
import shutil
import urllib.request
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path

from . import config as cfg

#: Manifest name used by the offline smoke-test fixture. Anything carrying
#: this name must never reach a published results table.
SYNTHETIC_NAME = "synthetic-fixture"


@dataclass
class DatasetManifest:
    name: str
    source_edges_url: str
    source_dates_url: str
    seed: int
    target_edges: int
    node_count: int
    edge_count: int
    distinct_years: int
    nodes_csv_sha256: str
    edges_csv_sha256: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path) -> Path:
    """Download `url` to `dest` unless it is already there."""
    if dest.exists():
        print(f"  cached  {dest.name}")
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    print(f"  fetch   {url}")
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(url, timeout=120) as resp, tmp.open("wb") as out:
        shutil.copyfileobj(resp, out)
    tmp.rename(dest)
    return dest


def _read_edges(gz_path: Path) -> list[tuple[int, int]]:
    """Parse a SNAP tab-separated edge list, skipping `#` comment lines."""
    edges: list[tuple[int, int]] = []
    with gzip.open(gz_path, "rt") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) != 2:
                continue
            edges.append((int(parts[0]), int(parts[1])))
    return edges


def _read_dates(gz_path: Path) -> dict[int, str]:
    """Parse cit-HepTh-dates.txt -> {node_id: 'YYYY-MM-DD'}.

    SNAP zero-pads some ids to 7 digits in this file while the edge list uses
    the unpadded integer, so we normalise through int().
    """
    dates: dict[int, str] = {}
    with gzip.open(gz_path, "rt") as fh:
        for line in fh:
            if line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) != 2:
                continue
            try:
                dates[int(parts[0])] = parts[1]
            except ValueError:
                continue
    return dates


def snowball(
    edges: list[tuple[int, int]], target_edges: int, seed: int
) -> tuple[list[int], list[tuple[int, int]]]:
    """Breadth-first sample until the induced subgraph has >= target_edges.

    Returns (nodes, edges) of the induced subgraph. Deterministic given `seed`:
    the start node is chosen from the highest-degree decile (so we do not start
    in a dangling corner of the graph) and the frontier is expanded in sorted
    order.
    """
    adjacency: dict[int, list[int]] = {}
    for src, dst in edges:
        adjacency.setdefault(src, []).append(dst)
        adjacency.setdefault(dst, []).append(src)

    rng = random.Random(seed)
    by_degree = sorted(adjacency, key=lambda n: (-len(adjacency[n]), n))
    top_decile = by_degree[: max(1, len(by_degree) // 10)]
    start = rng.choice(top_decile)

    selected: set[int] = {start}
    queue: deque[int] = deque([start])
    # Running count of edges whose endpoints are both selected. Incremented as
    # each new node is admitted, which is cheaper than recomputing the induced
    # subgraph on every step.
    induced_edges = 0

    while queue and induced_edges < target_edges:
        node = queue.popleft()
        for neighbour in sorted(adjacency.get(node, ())):
            if neighbour in selected:
                continue
            selected.add(neighbour)
            queue.append(neighbour)
            induced_edges += sum(1 for n in adjacency[neighbour] if n in selected)
            if induced_edges >= target_edges:
                break

    # Rebuild the *directed* edges from the original list, restricted to the
    # selected node set, preserving the file's original order.
    kept: list[tuple[int, int]] = []
    for src, dst in edges:
        if src in selected and dst in selected:
            kept.append((src, dst))
            if len(kept) >= target_edges:
                break

    nodes = sorted({n for edge in kept for n in edge})
    return nodes, kept


def prepare(force: bool = False) -> DatasetManifest:
    """Download, sample and write nodes.csv / edges.csv + manifest.json."""
    cfg.DATA_DIR.mkdir(parents=True, exist_ok=True)

    if cfg.MANIFEST_JSON.exists() and not force:
        cached = DatasetManifest(**json.loads(cfg.MANIFEST_JSON.read_text()))
        # The smoke-test fixture writes a manifest too. If we let that satisfy
        # the cache check, `bench dataset` after `bench selftest` would return
        # a synthetic graph while reporting success, and the entire benchmark
        # would run against fake data that looks real in every log line.
        if cached.name != SYNTHETIC_NAME:
            print("Dataset already prepared (use --force to rebuild).")
            return cached
        print("  cached manifest is the synthetic fixture — rebuilding for real.")

    edges_gz = _download(cfg.EDGES_URL, cfg.DATA_DIR / "cit-HepTh.txt.gz")
    dates_gz = _download(cfg.DATES_URL, cfg.DATA_DIR / "cit-HepTh-dates.txt.gz")

    print("  parse   edge list")
    all_edges = _read_edges(edges_gz)
    print(f"          {len(all_edges):,} edges in the full graph")

    print(f"  sample  snowball to >= {cfg.TARGET_EDGES:,} edges (seed={cfg.SEED})")
    nodes, edges = snowball(all_edges, cfg.TARGET_EDGES, cfg.SEED)
    print(f"          {len(nodes):,} nodes / {len(edges):,} relationships")

    dates = _read_dates(dates_gz)
    rng = random.Random(cfg.SEED)

    # Every node needs a `year` so the filtered-lookup and group-by workloads
    # have identical selectivity on every platform. ~1.5% of cit-HepTh nodes
    # have no date; we assign those a deterministic pseudo-year and record how
    # many were imputed in the manifest-adjacent README caveat.
    years: dict[int, int] = {}
    for node in nodes:
        raw = dates.get(node)
        if raw:
            years[node] = int(raw.split("-")[0])
        else:
            years[node] = rng.randint(1992, 2003)

    with cfg.NODES_CSV.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "year", "title"])
        for node in nodes:
            # `title` is filler payload so that nodes are not degenerate
            # 2-property objects; it is never queried on its own.
            writer.writerow([node, years[node], f"paper-{node}"])

    with cfg.EDGES_CSV.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["src", "dst"])
        writer.writerows(edges)

    manifest = DatasetManifest(
        name=cfg.DATASET_NAME,
        source_edges_url=cfg.EDGES_URL,
        source_dates_url=cfg.DATES_URL,
        seed=cfg.SEED,
        target_edges=cfg.TARGET_EDGES,
        node_count=len(nodes),
        edge_count=len(edges),
        distinct_years=len(set(years.values())),
        nodes_csv_sha256=_sha256(cfg.NODES_CSV),
        edges_csv_sha256=_sha256(cfg.EDGES_CSV),
    )
    cfg.MANIFEST_JSON.write_text(manifest.to_json())
    print(f"  wrote   {cfg.NODES_CSV.name}, {cfg.EDGES_CSV.name}, manifest.json")
    return manifest


def make_fixture(node_count: int = 2_000, edges_per_node: int = 8) -> DatasetManifest:
    """Generate a small synthetic graph so the harness can be exercised offline.

    This exists for smoke tests and for CI runners with no outbound network.
    The manifest name is set to `synthetic-fixture`, and the reporter prints a
    loud banner if any published result was produced from it -- a synthetic
    graph has none of the degree skew of a real citation network and would
    quietly make every traversal number optimistic.
    """
    cfg.DATA_DIR.mkdir(parents=True, exist_ok=True)
    rng = random.Random(cfg.SEED)
    nodes = list(range(1, node_count + 1))

    edges: list[tuple[int, int]] = []
    for node in nodes:
        # Preferential-ish attachment: link to a few lower-numbered nodes so
        # the graph is directed, connected and has some hub structure.
        for _ in range(rng.randint(1, edges_per_node)):
            target = rng.randint(1, max(1, node - 1)) if node > 1 else 1
            if target != node:
                edges.append((node, target))

    years = {n: rng.randint(1992, 2003) for n in nodes}

    with cfg.NODES_CSV.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["id", "year", "title"])
        for node in nodes:
            writer.writerow([node, years[node], f"paper-{node}"])

    with cfg.EDGES_CSV.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["src", "dst"])
        writer.writerows(edges)

    manifest = DatasetManifest(
        name=SYNTHETIC_NAME,
        source_edges_url="generated locally",
        source_dates_url="generated locally",
        seed=cfg.SEED,
        target_edges=len(edges),
        node_count=len(nodes),
        edge_count=len(edges),
        distinct_years=len(set(years.values())),
        nodes_csv_sha256=_sha256(cfg.NODES_CSV),
        edges_csv_sha256=_sha256(cfg.EDGES_CSV),
    )
    cfg.MANIFEST_JSON.write_text(manifest.to_json())
    print(
        f"  fixture {len(nodes):,} nodes / {len(edges):,} edges "
        f"(SYNTHETIC — not for published results)"
    )
    return manifest


def load_manifest() -> DatasetManifest:
    if not cfg.MANIFEST_JSON.exists():
        raise RuntimeError("Run `python -m bench dataset` first.")
    return DatasetManifest(**json.loads(cfg.MANIFEST_JSON.read_text()))


def read_nodes() -> list[dict]:
    with cfg.NODES_CSV.open() as fh:
        return [
            {"id": int(r["id"]), "year": int(r["year"]), "title": r["title"]}
            for r in csv.DictReader(fh)
        ]


def read_edges() -> list[dict]:
    with cfg.EDGES_CSV.open() as fh:
        return [{"src": int(r["src"]), "dst": int(r["dst"])} for r in csv.DictReader(fh)]


def batched(items: list, size: int):
    """Yield `items` in lists of at most `size`."""
    for i in range(0, len(items), size):
        yield items[i : i + size]