"""FalkorDB adapter.

FalkorDB speaks Cypher but executes it as sparse matrix algebra over a Redis
core, and it is *not* a Bolt server, so it needs its own client. The queries
below are kept as close to the Bolt ones as the dialect allows; the two known
divergences are documented in docs/QUERY-PARITY.md:

  * FalkorDB has no CREATE CONSTRAINT ... IS UNIQUE with the Neo4j syntax; we
    use its own constraint API plus an exact-match index.
  * Parameterised LIMIT is not supported, so the limit is interpolated into
    the query string. It is a constant in this benchmark, so this does not
    change the work done, but it does mean FalkorDB re-plans less often than
    the Bolt engines. Called out in the analysis.
"""

from __future__ import annotations

import time
from typing import Any, Sequence

from falkordb import FalkorDB

from .. import config as cfg
from .base import Adapter, LoadResult, chunk

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

Q_HOP = """
MATCH (a:Paper {id: $id})-[:CITES*%d..%d]->(b:Paper)
RETURN DISTINCT b.id AS id
"""

Q_POINT = "MATCH (p:Paper {id: $id}) RETURN p.id, p.year, p.title"
Q_FILTERED = "MATCH (p:Paper) WHERE p.year = $year RETURN p.id LIMIT %d"
Q_AGG = "MATCH (p:Paper) RETURN p.year AS year, count(*) AS papers ORDER BY year"
Q_WRITE = "MATCH (p:Paper {id: $id}) SET p.touched = $marker"


class FalkorDBAdapter(Adapter):
    def __init__(self, key: str, display_name: str, env_prefix: str):
        self.key = key
        self.display_name = display_name
        self.host = cfg.env(env_prefix, "HOST", "localhost")
        self.port = int(cfg.env(env_prefix, "PORT", "6379"))
        self.password = cfg.env(env_prefix, "PASSWORD", "")
        self.graph_name = cfg.env(env_prefix, "GRAPH", "bench")
        self.uri = f"redis://{self.host}:{self.port}"
        self._client = None
        self._graph = None

    def endpoint_host_port(self):
        return self.host, self.port

    def connect(self) -> None:
        self._client = FalkorDB(
            host=self.host,
            port=self.port,
            password=self.password or None,
        )
        self._graph = self._client.select_graph(self.graph_name)
        # Cheap liveness check that also forces the connection open.
        self._graph.query("RETURN 1")

    def close(self) -> None:
        self._client = None
        self._graph = None

    def _run(self, query: str, params: dict | None = None) -> list[Any]:
        result = self._graph.query(query, params or {})
        # `result_set` is already materialised by the client.
        return list(result.result_set)

    def reset(self) -> None:
        try:
            self._graph.delete()
        except Exception:  # noqa: BLE001 - graph may not exist yet
            pass
        self._graph = self._client.select_graph(self.graph_name)

    def create_schema(self) -> list[str]:
        applied: list[str] = []
        for stmt in (
            "CREATE INDEX FOR (p:Paper) ON (p.id)",
            "CREATE INDEX FOR (p:Paper) ON (p.year)",
        ):
            try:
                self._run(stmt)
                applied.append(stmt)
            except Exception as exc:  # noqa: BLE001
                applied.append(f"{stmt}  -- skipped: {type(exc).__name__}")
        return applied

    def load(
        self, nodes: Sequence[dict], edges: Sequence[dict], batch_size: int
    ) -> LoadResult:
        t0 = time.perf_counter()
        for batch in chunk(nodes, batch_size):
            self._run(Q_CREATE_NODES, {"rows": list(batch)})
        t1 = time.perf_counter()
        for batch in chunk(edges, batch_size):
            self._run(Q_CREATE_RELS, {"rows": list(batch)})
        t2 = time.perf_counter()
        return LoadResult(
            nodes_loaded=len(nodes),
            relationships_loaded=len(edges),
            node_seconds=t1 - t0,
            relationship_seconds=t2 - t1,
            total_seconds=t2 - t0,
            method="falkordb-py client, UNWIND-batched CREATE over RESP",
            batch_size=batch_size,
        )

    def q_hop(self, start_id: int, depth: int) -> list[Any]:
        return self._run(Q_HOP % (depth, depth), {"id": start_id})

    def q_point_lookup(self, node_id: int) -> list[Any]:
        return self._run(Q_POINT, {"id": node_id})

    def q_filtered_lookup(self, year: int, limit: int) -> list[Any]:
        return self._run(Q_FILTERED % limit, {"year": year})

    def q_aggregation(self) -> list[Any]:
        return self._run(Q_AGG)

    def w_upsert(self, node_id: int, marker: int) -> None:
        self._run(Q_WRITE, {"id": node_id, "marker": marker})

    def count_graph(self) -> tuple[int, int]:
        nodes = self._run("MATCH (n:Paper) RETURN count(n)")[0][0]
        rels = self._run("MATCH ()-[r:CITES]->() RETURN count(r)")[0][0]
        return int(nodes), int(rels)

    def footprint(self) -> dict[str, Any]:
        out: dict[str, Any] = {"advertised": "0.5 vCPU / 256 MB (cgroup)"}
        try:
            info = self._client.connection.info("memory")
            out["used_memory_human"] = info.get("used_memory_human", "unknown")
            out["used_memory_bytes"] = info.get("used_memory", "unknown")
        except Exception as exc:  # noqa: BLE001
            out["memory"] = f"not observable ({type(exc).__name__})"
        try:
            out["graph_memory"] = self._run("CALL dbms.procedures()")[:3]
        except Exception:  # noqa: BLE001
            out["graph_memory"] = "not observable"
        return out
