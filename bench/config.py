"""Central knobs for the benchmark.

Two rules I tried to hold to here:

1. Anything that changes what is *measured* lives in this file, not scattered
   through the workload code, so that a reviewer can audit the whole
   methodology by reading one screen.
2. Anything secret (URIs, passwords) comes from the environment and never from
   this file. See `.env.example`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RESULTS_DIR = ROOT / "results"
RAW_DIR = RESULTS_DIR / "raw"
CHART_DIR = RESULTS_DIR / "charts"

# --------------------------------------------------------------------------
# Dataset
# --------------------------------------------------------------------------

# SNAP arXiv HEP-TH citation network. Chosen because it is small to download,
# genuinely directed, has real citation structure (so multi-hop traversals
# actually fan out), and ships a companion file of publication dates that
# gives us a non-synthetic property to index and group by.
DATASET_NAME = "cit-HepTh"
EDGES_URL = "https://snap.stanford.edu/data/cit-HepTh.txt.gz"
DATES_URL = "https://snap.stanford.edu/data/cit-HepTh-dates.txt.gz"

# The full graph is ~352k edges. We snowball-sample down to this many so that
# it fits in a 256 MB instance on *every* platform. Change it and every
# platform must be reloaded from scratch.
#
# Why 100k and not more: Memgraph stores the whole graph in RAM with no disk
# to spill to, and refused a 200k-relationship load at 230 MiB with
# "Memory limit exceeded". The disk-backed engines (CognoDB, Aura, ArangoDB)
# had no such trouble -- they page to their 1 GB volume. Since the benchmark
# requires one identical dataset everywhere, the most memory-constrained
# engine sets the size for all of them. That Memgraph is the binding
# constraint is itself a result, and is written up in the README.
#
# 100k keeps us at the assignment's stated minimum of 100,000 relationships.
TARGET_EDGES = int(os.getenv("BENCH_TARGET_EDGES", "100000"))

# Seed for sampling, start-node selection and the mixed-workload RNG. Fixed so
# that two people running this benchmark measure the same thing.
SEED = 42

NODES_CSV = DATA_DIR / f"{DATASET_NAME}-nodes.csv"
EDGES_CSV = DATA_DIR / f"{DATASET_NAME}-edges.csv"
MANIFEST_JSON = DATA_DIR / "manifest.json"

# --------------------------------------------------------------------------
# Workload shape
# --------------------------------------------------------------------------

# Iterations per read workload *after* warm-up. The assignment asks for >= 100;
# 300 gives noticeably tighter p95s without making a full sweep take all day.
ITERATIONS = int(os.getenv("BENCH_ITERATIONS", "300"))

# Warm-up iterations. These are not thrown away -- they are recorded and
# reported separately as the "cold" series, which is free information.
WARMUP = int(os.getenv("BENCH_WARMUP", "30"))

# How many times to repeat the whole read suite. Multiple repeats let us report
# run-to-run variance instead of pretending one run is the truth.
REPEATS = int(os.getenv("BENCH_REPEATS", "3"))

# Client concurrency levels for the mixed workload sweep.
CONCURRENCY_LEVELS = tuple(
    int(x) for x in os.getenv("BENCH_CONCURRENCY", "1,10,40").split(",")
)

# Seconds to sustain the mixed workload at each concurrency level.
MIXED_DURATION_S = float(os.getenv("BENCH_MIXED_DURATION", "60"))

# Fraction of the mixed workload that is reads. 0.9 -> 90% read / 10% write.
READ_RATIO = float(os.getenv("BENCH_READ_RATIO", "0.9"))

# Rows per batch during ingest. Same value for every platform; see README for
# why we did not tune this per-platform.
#
# 2,000 rather than 5,000: on a 256 MB instance the in-flight transaction is a
# meaningful fraction of the budget, and the largest batch that succeeds is
# not the same as the largest batch that is fair. A smaller batch costs some
# ingest throughput on every platform equally, which is the trade we want.
BATCH_SIZE = int(os.getenv("BENCH_BATCH_SIZE", "2000"))

# Number of distinct start nodes drawn for the traversal workloads. Drawn once,
# persisted, and replayed identically against every platform.
START_NODE_POOL = 200

# A single query that exceeds this is recorded as a timeout and excluded from
# percentiles (but counted, and surfaced in the report -- never silently).
QUERY_TIMEOUT_S = float(os.getenv("BENCH_QUERY_TIMEOUT", "30"))

# --------------------------------------------------------------------------
# Platforms
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PlatformSpec:
    """Everything we claim about a platform, in one place.

    `advertised_*` fields are transcribed from each vendor's public docs and
    are reproduced in the README results matrix. If a value cannot be verified
    it must be the string "unknown" rather than a guess.
    """

    key: str
    display_name: str
    engine: str
    query_language: str
    deployment: str  # "managed-free-tier" | "self-hosted-capped"
    advertised_vcpu: str
    advertised_ram: str
    advertised_disk: str
    env_prefix: str
    notes: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


PLATFORMS: dict[str, PlatformSpec] = {
    "cognodb": PlatformSpec(
        key="cognodb",
        display_name="CognoDB Cloud (c0 free)",
        engine="CognoDB",
        query_language="Cypher (Bolt)",
        deployment="managed-free-tier",
        advertised_vcpu="0.5 (burstable)",
        advertised_ram="256 MB",
        advertised_disk="1 GB",
        env_prefix="COGNODB",
        notes="Reference tier for this benchmark. Every other platform is "
        "capped to match it as closely as its tier allows.",
        tags=("bolt",),
    ),
    "neo4j_aura": PlatformSpec(
        key="neo4j_aura",
        display_name="Neo4j AuraDB Free",
        engine="Neo4j 5.x",
        query_language="Cypher (Bolt)",
        deployment="managed-free-tier",
        advertised_vcpu="not published (shared)",
        advertised_ram="not published (shared)",
        advertised_disk="200k nodes / 400k relationships cap",
        env_prefix="AURA",
        notes="Aura Free does not expose CPU/RAM and cannot be capped by the "
        "user. This is the one unavoidable parity gap in the suite and is "
        "called out in the README fairness section.",
        tags=("bolt",),
    ),
    "memgraph": PlatformSpec(
        key="memgraph",
        display_name="Memgraph 2.x (self-hosted, capped)",
        engine="Memgraph (in-memory, C++)",
        query_language="Cypher (Bolt)",
        deployment="self-hosted-capped",
        advertised_vcpu="0.5 (cgroup limit)",
        advertised_ram="256 MB (cgroup limit)",
        advertised_disk="1 GB volume",
        env_prefix="MEMGRAPH",
        notes="Capped via docker --cpus/--memory to match the c0 tier.",
        tags=("bolt",),
    ),
    "falkordb": PlatformSpec(
        key="falkordb",
        display_name="FalkorDB (self-hosted, capped)",
        engine="FalkorDB (sparse linear algebra over Redis)",
        query_language="Cypher (RESP)",
        deployment="self-hosted-capped",
        advertised_vcpu="0.5 (cgroup limit)",
        advertised_ram="256 MB (cgroup limit)",
        advertised_disk="1 GB volume",
        env_prefix="FALKORDB",
        notes="Same query language, radically different execution model -- "
        "included to separate 'Cypher' from 'the engine underneath it'.",
        tags=("cypher",),
    ),
    "arangodb": PlatformSpec(
        key="arangodb",
        display_name="ArangoDB 3.11 (self-hosted, capped)",
        engine="ArangoDB (multi-model, RocksDB)",
        query_language="AQL (HTTP)",
        deployment="self-hosted-capped",
        advertised_vcpu="0.5 (cgroup limit)",
        advertised_ram="256 MB (cgroup limit)",
        advertised_disk="1 GB volume",
        env_prefix="ARANGO",
        notes="The only non-Cypher engine in the suite. Its queries are "
        "hand-translated to AQL; see docs/QUERY-PARITY.md.",
        tags=("aql",),
    ),
    "mock": PlatformSpec(
        key="mock",
        display_name="Mock backend (harness self-test)",
        engine="in-process Python dict",
        query_language="n/a",
        deployment="local",
        advertised_vcpu="n/a",
        advertised_ram="n/a",
        advertised_disk="n/a",
        env_prefix="MOCK",
        notes="Not a database. Exists so the harness can be exercised in CI "
        "without credentials. Never include it in published results.",
        tags=(),
    ),
}

# Platforms that appear in the published results matrix.
REPORTED_PLATFORMS = tuple(k for k in PLATFORMS if k != "mock")


def env(prefix: str, name: str, default: str | None = None) -> str:
    """Read `<PREFIX>_<NAME>` from the environment.

    Raises rather than returning a placeholder: a benchmark that silently
    connects to the wrong endpoint is worse than one that refuses to start.
    """
    key = f"{prefix}_{name}"
    value = os.getenv(key, default)
    if value is None:
        raise RuntimeError(
            f"Missing environment variable {key}. "
            f"Copy .env.example to .env and fill it in."
        )
    return value