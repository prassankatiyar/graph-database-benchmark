"""An in-process fake graph, used only to exercise the harness.

This exists so that `make selftest` can run the entire pipeline -- load,
warm-up, percentiles, concurrency sweep, report generation -- on a laptop with
no credentials and no containers. It deliberately adds a small artificial
delay so that latency series are non-degenerate.

It is excluded from every published table. If you ever see "mock" in a results
matrix, the results are wrong.
"""

from __future__ import annotations

import random
import time
from collections import defaultdict
from typing import Any, Sequence

from .base import Adapter, LoadResult


class MockAdapter(Adapter):
    def __init__(self, key: str = "mock", display_name: str = "Mock", env_prefix: str = "MOCK"):
        self.key = key
        self.display_name = display_name
        self.uri = None
        self._nodes: dict[int, dict] = {}
        self._out: dict[int, list[int]] = defaultdict(list)
        self._rel_count = 0
        self._rng = random.Random(7)

    def endpoint_host_port(self):
        return None

    def connect(self) -> None:
        pass

    def close(self) -> None:
        pass

    def reset(self) -> None:
        self._nodes.clear()
        self._out.clear()
        self._rel_count = 0

    def create_schema(self) -> list[str]:
        return ["(mock) dict keyed by id; no real indexes"]

    def load(self, nodes: Sequence[dict], edges: Sequence[dict], batch_size: int) -> LoadResult:
        t0 = time.perf_counter()
        for n in nodes:
            self._nodes[n["id"]] = n
        t1 = time.perf_counter()
        for e in edges:
            self._out[e["src"]].append(e["dst"])
            self._rel_count += 1
        t2 = time.perf_counter()
        return LoadResult(
            nodes_loaded=len(nodes),
            relationships_loaded=len(edges),
            node_seconds=max(t1 - t0, 1e-6),
            relationship_seconds=max(t2 - t1, 1e-6),
            total_seconds=max(t2 - t0, 1e-6),
            method="in-process dict insert (not a database)",
            batch_size=batch_size,
        )

    def _jitter(self) -> None:
        # Log-normal-ish delay so the percentile code sees a realistic tail.
        time.sleep(abs(self._rng.gauss(0.0004, 0.0003)))

    def q_hop(self, start_id: int, depth: int) -> list[Any]:
        frontier = {start_id}
        for _ in range(depth):
            frontier = {d for s in frontier for d in self._out.get(s, ())}
        self._jitter()
        return [{"id": i} for i in frontier]

    def q_point_lookup(self, node_id: int) -> list[Any]:
        self._jitter()
        node = self._nodes.get(node_id)
        return [node] if node else []

    def q_filtered_lookup(self, year: int, limit: int) -> list[Any]:
        self._jitter()
        out = [n for n in self._nodes.values() if n["year"] == year]
        return out[:limit]

    def q_aggregation(self) -> list[Any]:
        self._jitter()
        counts: dict[int, int] = defaultdict(int)
        for n in self._nodes.values():
            counts[n["year"]] += 1
        return [{"year": y, "papers": c} for y, c in sorted(counts.items())]

    def w_upsert(self, node_id: int, marker: int) -> None:
        self._jitter()
        if node_id in self._nodes:
            self._nodes[node_id]["touched"] = marker

    def count_graph(self) -> tuple[int, int]:
        return len(self._nodes), self._rel_count

    def footprint(self) -> dict[str, Any]:
        return {"store_size": "not observable", "note": "mock backend"}
