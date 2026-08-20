"""Bolt + Cypher adapter.

Covers CognoDB Cloud, Neo4j AuraDB and Memgraph. All three speak Bolt and all
three accept the official `neo4j` Python driver, so they share one code path
and one set of query strings. That is the whole point: three engines running
byte-identical Cypher removes "you wrote nicer queries for your favourite" as
an explanation for any difference in the numbers.

The only per-engine divergence is in schema DDL (Memgraph's index syntax
differs from Neo4j's) and in `footprint()`, and both are isolated in
subclasses below.
"""

from __future__ import annotations

import time
from typing import Any, Sequence

from neo4j import GraphDatabase, basic_auth

from .. import config as cfg
from .base import Adapter, LoadResult, chunk

# --------------------------------------------------------------------------
# Query text. Shared verbatim by every Bolt platform.
# --------------------------------------------------------------------------

Q_CREATE_NODES = """
UNWIND $rows AS row
CREATE (p:Paper {id: row.id, year: row.year, title: row.title})
"""

Q_CREATE_RELS = """
UNWIND $rows AS row
MATCH (a:Paper {id: row.src})
MATCH (b:Paper {id: row.dst})
CREATE (a)-[:CITES]->(b)
"""

# Exactly-N-hops, deduplicated. Written with an explicit variable-length
# pattern of fixed depth rather than repeated MATCH clauses so that the
# planner sees the same shape at every depth.
Q_HOP = """
MATCH (a:Paper {id: $id})-[:CITES*%d..%d]->(b:Paper)
RETURN DISTINCT b.id AS id
"""

Q_POINT = "MATCH (p:Paper {id: $id}) RETURN p.id AS id, p.year AS year, p.title AS title"

Q_FILTERED = """
MATCH (p:Paper)
WHERE p.year = $year
RETURN p.id AS id
LIMIT $limit
"""

Q_AGG = """
MATCH (p:Paper)
RETURN p.year AS year, count(*) AS papers
ORDER BY year
"""

Q_WRITE = """
MATCH (p:Paper {id: $id})
SET p.touched = $marker
"""

Q_COUNTS_NODES = "MATCH (n:Paper) RETURN count(n) AS c"
Q_COUNTS_RELS = "MATCH ()-[r:CITES]->() RETURN count(r) AS c"


class BoltAdapter(Adapter):
    """Base Bolt implementation. Subclass per platform for DDL and footprint."""

    #: Neo4j-style DDL. Overridden by Memgraph.
    schema_statements: tuple[str, ...] = (
        "CREATE CONSTRAINT paper_id IF NOT EXISTS "
        "FOR (p:Paper) REQUIRE p.id IS UNIQUE",
        "CREATE INDEX paper_year IF NOT EXISTS FOR (p:Paper) ON (p.year)",
    )

    def __init__(self, key: str, display_name: str, env_prefix: str):
        self.key = key
        self.display_name = display_name
        self.uri = cfg.env(env_prefix, "URI")
        self.user = cfg.env(env_prefix, "USER")
        self.password = cfg.env(env_prefix, "PASSWORD")
        self.database = cfg.env(env_prefix, "DATABASE", "neo4j")
        self._driver = None

    # -- lifecycle ---------------------------------------------------------

    def connect(self) -> None:
        self._driver = GraphDatabase.driver(
            self.uri,
            auth=basic_auth(self.user, self.password),
            # One connection per client thread; the mixed workload opens up to
            # 40, so the pool must not become the bottleneck we are measuring.
            max_connection_pool_size=64,
            connection_acquisition_timeout=60,
            max_transaction_retry_time=15,
        )
        self._driver.verify_connectivity()

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def _run(self, query: str, **params) -> list[Any]:
        """Run a query and fully drain the result before returning."""
        with self._driver.session(database=self.database) as session:
            result = session.run(query, **params)
            return [record.data() for record in result]

    # -- schema ------------------------------------------------------------

    def reset(self) -> None:
        # Deleting a 200k-relationship graph in one transaction will blow a
        # 256 MB heap, so we do it in bounded batches.
        while True:
            deleted = self._run(
                "MATCH (n) WITH n LIMIT 10000 DETACH DELETE n RETURN count(n) AS c"
            )
            if not deleted or deleted[0]["c"] == 0:
                break

    def create_schema(self) -> list[str]:
        applied = []
        for stmt in self.schema_statements:
            self._run(stmt)
            applied.append(stmt)
        return applied

    # -- ingest ------------------------------------------------------------

    def load(
        self, nodes: Sequence[dict], edges: Sequence[dict], batch_size: int
    ) -> LoadResult:
        t0 = time.perf_counter()
        for batch in chunk(nodes, batch_size):
            self._run(Q_CREATE_NODES, rows=list(batch))
        t1 = time.perf_counter()
        for batch in chunk(edges, batch_size):
            self._run(Q_CREATE_RELS, rows=list(batch))
        t2 = time.perf_counter()
        return LoadResult(
            nodes_loaded=len(nodes),
            relationships_loaded=len(edges),
            node_seconds=t1 - t0,
            relationship_seconds=t2 - t1,
            total_seconds=t2 - t0,
            method=(
                "official neo4j Python driver, UNWIND-batched CREATE over Bolt; "
                "relationships matched on the unique :Paper(id) constraint"
            ),
            batch_size=batch_size,
        )

    # -- reads -------------------------------------------------------------

    def q_hop(self, start_id: int, depth: int) -> list[Any]:
        return self._run(Q_HOP % (depth, depth), id=start_id)

    def q_point_lookup(self, node_id: int) -> list[Any]:
        return self._run(Q_POINT, id=node_id)

    def q_filtered_lookup(self, year: int, limit: int) -> list[Any]:
        return self._run(Q_FILTERED, year=year, limit=limit)

    def q_aggregation(self) -> list[Any]:
        return self._run(Q_AGG)

    def w_upsert(self, node_id: int, marker: int) -> None:
        self._run(Q_WRITE, id=node_id, marker=marker)

    # -- introspection -----------------------------------------------------

    def count_graph(self) -> tuple[int, int]:
        nodes = self._run(Q_COUNTS_NODES)[0]["c"]
        rels = self._run(Q_COUNTS_RELS)[0]["c"]
        return nodes, rels

    def footprint(self) -> dict[str, Any]:
        return {"store_size": "not observable", "memory": "not observable"}


class CognoDBAdapter(BoltAdapter):
    """CognoDB Cloud free (c0) instance."""

    def footprint(self) -> dict[str, Any]:
        # CognoDB is Neo4j-protocol compatible, so we try the standard
        # introspection procedures and degrade honestly if they are not
        # exposed on the free tier.
        out: dict[str, Any] = {}
        for label, query in (
            ("store_size", "CALL dbms.queryJmx('org.neo4j:*') YIELD name RETURN name LIMIT 1"),
            ("components", "CALL dbms.components() YIELD name, versions, edition "
                           "RETURN name, versions, edition"),
        ):
            try:
                out[label] = self._run(query)
            except Exception as exc:  # noqa: BLE001 - we want the reason in the report
                out[label] = f"not observable ({type(exc).__name__})"
        out["advertised"] = "0.5 vCPU burstable / 256 MB RAM / 1 GB disk (console)"
        return out


class AuraAdapter(BoltAdapter):
    """Neo4j AuraDB Free."""

    def footprint(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        try:
            out["components"] = self._run(
                "CALL dbms.components() YIELD name, versions, edition "
                "RETURN name, versions, edition"
            )
        except Exception as exc:  # noqa: BLE001
            out["components"] = f"not observable ({type(exc).__name__})"
        # Aura Free deliberately hides host metrics; the console shows a
        # node/relationship usage bar and nothing else.
        out["store_size"] = "not observable (Aura Free exposes no host metrics)"
        out["memory"] = "not observable"
        out["advertised"] = "shared infrastructure; 200k node / 400k relationship cap"
        return out


class MemgraphAdapter(BoltAdapter):
    """Memgraph, self-hosted under a cgroup cap."""

    # Memgraph's DDL is not Neo4j's. Same intent: unique id, secondary index
    # on year.
    schema_statements = (
        "CREATE CONSTRAINT ON (p:Paper) ASSERT p.id IS UNIQUE",
        "CREATE INDEX ON :Paper(id)",
        "CREATE INDEX ON :Paper(year)",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Memgraph has no notion of multiple databases in the community
        # edition; passing a database name makes the driver send a selector it
        # rejects.
        self.database = None

    def create_schema(self) -> list[str]:
        applied = []
        for stmt in self.schema_statements:
            try:
                self._run(stmt)
                applied.append(stmt)
            except Exception as exc:  # noqa: BLE001
                # An existing constraint is not an error worth aborting for,
                # but it is worth recording.
                applied.append(f"{stmt}  -- skipped: {type(exc).__name__}")
        return applied

    def reset(self) -> None:
        self._run("MATCH (n) DETACH DELETE n")

    def footprint(self) -> dict[str, Any]:
        try:
            rows = self._run("SHOW STORAGE INFO")
            return {"storage_info": rows, "advertised": "0.5 vCPU / 256 MB (cgroup)"}
        except Exception as exc:  # noqa: BLE001
            return {"storage_info": f"not observable ({type(exc).__name__})"}
