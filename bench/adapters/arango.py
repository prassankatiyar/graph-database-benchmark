"""ArangoDB adapter.

The odd one out: no Cypher, no Bolt. Queries are hand-translated to AQL and
the translations are documented side by side in docs/QUERY-PARITY.md so a
reader can check that "1-hop in Cypher" and "1-hop in AQL" really are asking
for the same rows.

Two things worth knowing when reading the numbers:

  * ArangoDB is document-first. Nodes are documents in a `papers` collection
    keyed by `_key`, edges are documents in a `cites` edge collection. The
    point lookup therefore hits a primary index the same way the Cypher
    engines hit theirs.
  * Traversal uses `FOR v IN d..d OUTBOUND` with `uniqueVertices: 'path'`.
    The first version of this file used `'global'`, which is wrong here: it
    forbids revisiting a vertex anywhere in the traversal, so a node reachable
    at both depth 1 and depth 3 is silently dropped from the depth-3 result.
    Cypher's `[:CITES*3..3]` followed by `DISTINCT` keeps it. The bug showed
    up as ArangoDB returning 601 rows for a 3-hop query where every Cypher
    engine returned 765 -- caught by the result-set parity table in
    RESULTS.md, which exists for precisely this.
"""

from __future__ import annotations

import time
from typing import Any, Sequence

from arango import ArangoClient

from .. import config as cfg
from .base import Adapter, LoadResult, chunk

Q_HOP = """
FOR v IN @@depth..@@depth OUTBOUND @start cites
  OPTIONS { uniqueVertices: 'path', bfs: true }
  RETURN DISTINCT v.id
"""

Q_POINT = """
FOR p IN papers FILTER p._key == @key RETURN { id: p.id, year: p.year, title: p.title }
"""

Q_FILTERED = """
FOR p IN papers FILTER p.year == @year LIMIT @limit RETURN p.id
"""

Q_AGG = """
FOR p IN papers
  COLLECT year = p.year WITH COUNT INTO papers
  SORT year
  RETURN { year, papers }
"""

Q_WRITE = """
UPDATE @key WITH { touched: @marker } IN papers
"""


class ArangoAdapter(Adapter):
    def __init__(self, key: str, display_name: str, env_prefix: str):
        self.key = key
        self.display_name = display_name
        self.uri = cfg.env(env_prefix, "URI", "http://localhost:8529")
        self.user = cfg.env(env_prefix, "USER", "root")
        self.password = cfg.env(env_prefix, "PASSWORD")
        self.db_name = cfg.env(env_prefix, "DATABASE", "bench")
        self._client = None
        self._db = None

    def endpoint_host_port(self):
        from urllib.parse import urlparse

        parsed = urlparse(self.uri)
        return parsed.hostname or "localhost", parsed.port or 8529

    def connect(self) -> None:
        # Two defaults in python-arango are wrong for this benchmark:
        #
        # 1. request_timeout is 60 s. Dropping a 100k-document edge collection
        #    on 0.5 vCPU takes longer than that, so `reset()` would abandon a
        #    request the server was still happily working on.
        # 2. pool_maxsize is 10. The mixed workload runs up to 40 concurrent
        #    clients; with a pool of 10, three quarters of them would queue in
        #    the *client*, and we would be benchmarking urllib3 rather than
        #    ArangoDB. The Bolt adapter sets max_connection_pool_size=64 for
        #    the same reason.
        #
        # Constructed defensively because the http_client keyword has moved
        # between python-arango majors and a hard failure here is worse than
        # falling back to defaults we can then note in the caveats.
        client_kwargs = {"hosts": self.uri}
        try:
            from arango.http import DefaultHTTPClient

            client_kwargs["http_client"] = DefaultHTTPClient(
                request_timeout=300,
                pool_maxsize=64,
                pool_connections=64,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  warn    using default HTTP client ({type(exc).__name__})")

        self._client = ArangoClient(**client_kwargs)
        sys_db = self._client.db("_system", username=self.user, password=self.password)
        if not sys_db.has_database(self.db_name):
            sys_db.create_database(self.db_name)
        self._db = self._client.db(
            self.db_name, username=self.user, password=self.password
        )
        self._db.properties()  # forces a round trip; fails loudly on bad creds

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
        self._client = None
        self._db = None

    def _aql(self, query: str, **bind) -> list[Any]:
        cursor = self._db.aql.execute(query, bind_vars=bind)
        return list(cursor)  # drain, do not time a lazy cursor

    def reset(self) -> None:
        # Dropping a 100k-document edge collection on 0.5 vCPU is slow enough
        # that a client-side timeout can fire while the server is still
        # working. Retrying a drop that already succeeded is harmless, so we
        # re-check rather than abort the whole platform run.
        for name in ("cites", "papers"):
            for attempt in (1, 2):
                if not self._db.has_collection(name):
                    break
                try:
                    self._db.delete_collection(name)
                    break
                except Exception as exc:  # noqa: BLE001
                    if attempt == 2:
                        raise
                    print(
                        f"  warn    dropping '{name}' timed out "
                        f"({type(exc).__name__}); re-checking"
                    )
                    time.sleep(15)

    def create_schema(self) -> list[str]:
        applied = []
        if not self._db.has_collection("papers"):
            self._db.create_collection("papers")
            applied.append("CREATE COLLECTION papers (primary index on _key)")
        if not self._db.has_collection("cites"):
            self._db.create_collection("cites", edge=True)
            applied.append("CREATE EDGE COLLECTION cites (_from/_to indexes)")
        papers = self._db.collection("papers")
        papers.add_persistent_index(fields=["year"], name="idx_year", in_background=False)
        applied.append("CREATE PERSISTENT INDEX idx_year ON papers(year)")
        papers.add_persistent_index(fields=["id"], name="idx_id", in_background=False)
        applied.append("CREATE PERSISTENT INDEX idx_id ON papers(id)")
        return applied

    def load(
        self, nodes: Sequence[dict], edges: Sequence[dict], batch_size: int
    ) -> LoadResult:
        papers = self._db.collection("papers")
        cites = self._db.collection("cites")

        t0 = time.perf_counter()
        for batch in chunk(nodes, batch_size):
            papers.insert_many(
                [
                    {"_key": str(n["id"]), "id": n["id"], "year": n["year"], "title": n["title"]}
                    for n in batch
                ],
                overwrite=False,
            )
        t1 = time.perf_counter()
        for batch in chunk(edges, batch_size):
            cites.insert_many(
                [
                    {"_from": f"papers/{e['src']}", "_to": f"papers/{e['dst']}"}
                    for e in batch
                ],
                overwrite=False,
            )
        t2 = time.perf_counter()
        return LoadResult(
            nodes_loaded=len(nodes),
            relationships_loaded=len(edges),
            node_seconds=t1 - t0,
            relationship_seconds=t2 - t1,
            total_seconds=t2 - t0,
            method="python-arango insert_many() bulk document API over HTTP",
            batch_size=batch_size,
        )

    def q_hop(self, start_id: int, depth: int) -> list[Any]:
        # AQL cannot bind traversal depth, so it is formatted in. Depth is one
        # of {1,2,3} from our own config, never user input.
        query = Q_HOP.replace("@@depth..@@depth", f"{depth}..{depth}")
        return self._aql(query, start=f"papers/{start_id}")

    def q_point_lookup(self, node_id: int) -> list[Any]:
        return self._aql(Q_POINT, key=str(node_id))

    def q_filtered_lookup(self, year: int, limit: int) -> list[Any]:
        return self._aql(Q_FILTERED, year=year, limit=limit)

    def q_aggregation(self) -> list[Any]:
        return self._aql(Q_AGG)

    def w_upsert(self, node_id: int, marker: int) -> None:
        self._aql(Q_WRITE, key=str(node_id), marker=marker)

    def count_graph(self) -> tuple[int, int]:
        # `bench doctor` calls this before any load has happened, at which
        # point the collections do not exist. A missing collection means an
        # empty graph, not a broken connection -- reporting it as a failure
        # would send you debugging credentials that are actually fine.
        def _count(name: str) -> int:
            if not self._db.has_collection(name):
                return 0
            return self._db.collection(name).count()

        return _count("papers"), _count("cites")

    def footprint(self) -> dict[str, Any]:
        out: dict[str, Any] = {"advertised": "0.5 vCPU / 256 MB (cgroup)"}
        try:
            figures = self._db.collection("papers").figures()
            out["papers_figures"] = figures.get("figures", figures)
        except Exception as exc:  # noqa: BLE001
            out["papers_figures"] = f"not observable ({type(exc).__name__})"
        try:
            out["cites_figures"] = self._db.collection("cites").figures()
        except Exception as exc:  # noqa: BLE001
            out["cites_figures"] = f"not observable ({type(exc).__name__})"
        return out