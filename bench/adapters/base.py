"""The contract every database backend implements.

Design note: the adapter returns *rows*, and the harness times the call that
returns them. That is deliberate. Most drivers hand back a lazy cursor, and if
you stop the clock before draining it you are timing the round trip of a
request header and nothing else. Every method below must fully materialise its
result before returning.
"""

from __future__ import annotations

import abc
import socket
import time
from dataclasses import dataclass
from typing import Any, Iterable, Sequence
from urllib.parse import urlparse


@dataclass
class LoadResult:
    """Outcome of a full ingest."""

    nodes_loaded: int
    relationships_loaded: int
    node_seconds: float
    relationship_seconds: float
    total_seconds: float
    method: str  # human-readable description for the README
    batch_size: int

    @property
    def nodes_per_second(self) -> float:
        return self.nodes_loaded / self.node_seconds if self.node_seconds else 0.0

    @property
    def relationships_per_second(self) -> float:
        return (
            self.relationships_loaded / self.relationship_seconds
            if self.relationship_seconds
            else 0.0
        )

    def to_dict(self) -> dict:
        return {
            "nodes_loaded": self.nodes_loaded,
            "relationships_loaded": self.relationships_loaded,
            "node_seconds": round(self.node_seconds, 3),
            "relationship_seconds": round(self.relationship_seconds, 3),
            "total_seconds": round(self.total_seconds, 3),
            "nodes_per_second": round(self.nodes_per_second, 1),
            "relationships_per_second": round(self.relationships_per_second, 1),
            "method": self.method,
            "batch_size": self.batch_size,
        }


class Adapter(abc.ABC):
    """One database, one adapter.

    Lifecycle: connect() -> reset() -> create_schema() -> load() -> queries
    -> footprint() -> close().
    """

    #: Populated by the factory from config.PLATFORMS
    key: str = "unset"
    display_name: str = "unset"

    # -- lifecycle ---------------------------------------------------------

    @abc.abstractmethod
    def connect(self) -> None:
        """Open connections and fail loudly if credentials are wrong."""

    @abc.abstractmethod
    def close(self) -> None: ...

    @abc.abstractmethod
    def reset(self) -> None:
        """Delete all benchmark data. Must be safe to call on an empty store."""

    @abc.abstractmethod
    def create_schema(self) -> list[str]:
        """Create indexes/constraints. Returns the statements actually run.

        The returned list is printed into the README so that "which properties
        are indexed on each platform" is answered by the code, not by memory.
        """

    # -- ingest ------------------------------------------------------------

    @abc.abstractmethod
    def load(self, nodes: Sequence[dict], edges: Sequence[dict], batch_size: int) -> LoadResult: ...

    # -- read workloads ----------------------------------------------------
    # Each returns a list of rows; the harness times the call and records
    # len(rows) so that we can prove all platforms did the same amount of work.

    @abc.abstractmethod
    def q_hop(self, start_id: int, depth: int) -> list[Any]:
        """Distinct nodes reachable in exactly `depth` outgoing hops."""

    @abc.abstractmethod
    def q_point_lookup(self, node_id: int) -> list[Any]:
        """Fetch one node by its primary id."""

    @abc.abstractmethod
    def q_filtered_lookup(self, year: int, limit: int) -> list[Any]:
        """Fetch nodes by an indexed secondary property."""

    @abc.abstractmethod
    def q_aggregation(self) -> list[Any]:
        """Group-by count over the whole node label."""

    # -- write workload ----------------------------------------------------

    @abc.abstractmethod
    def w_upsert(self, node_id: int, marker: int) -> None:
        """The write half of the mixed workload.

        Must be idempotent and must not grow the graph without bound: we
        update a property on an existing node rather than inserting, so that a
        60-second write run does not change the dataset out from under the
        read half.
        """

    # -- introspection -----------------------------------------------------

    @abc.abstractmethod
    def footprint(self) -> dict[str, Any]:
        """Whatever the platform exposes about storage/memory.

        Return `{"<field>": "not observable"}` for anything the platform does
        not expose. Never estimate.
        """

    @abc.abstractmethod
    def count_graph(self) -> tuple[int, int]:
        """(node_count, relationship_count) as the database sees it.

        Used to verify that every platform actually holds the same graph
        before any latency number is believed.
        """

    # -- shared helpers ----------------------------------------------------

    def endpoint_host_port(self) -> tuple[str, int] | None:
        """Host/port used for the RTT probe. Override if the URI is unusual."""
        uri = getattr(self, "uri", None)
        if not uri:
            return None
        parsed = urlparse(uri)
        if not parsed.hostname:
            return None
        return parsed.hostname, parsed.port or 7687

    def tcp_rtt_ms(self, samples: int = 20) -> dict[str, float]:
        """Median TCP connect time to the endpoint.

        This is the floor under every latency number we report. Self-hosted
        containers on the client machine have an RTT near zero while a managed
        instance across a region does not, so the raw latencies are not
        comparable until you subtract this. We measure it rather than assume
        it.
        """
        target = self.endpoint_host_port()
        if target is None:
            return {"median_ms": float("nan"), "note": "endpoint not introspectable"}
        host, port = target
        timings: list[float] = []
        for _ in range(samples):
            start = time.perf_counter_ns()
            try:
                with socket.create_connection((host, port), timeout=5):
                    pass
            except OSError:
                continue
            timings.append((time.perf_counter_ns() - start) / 1e6)
        if not timings:
            return {"median_ms": float("nan"), "note": "probe failed"}
        timings.sort()
        return {
            "median_ms": round(timings[len(timings) // 2], 3),
            "min_ms": round(timings[0], 3),
            "samples": len(timings),
        }

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def chunk(items: Sequence[dict], size: int) -> Iterable[Sequence[dict]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]
