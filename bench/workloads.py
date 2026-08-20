"""The measurement loops themselves.

Everything that touches a clock lives here. Three rules held throughout:

1. `time.perf_counter_ns()` around the adapter call, and the adapter call
   returns materialised rows -- we never stop the clock on a lazy cursor.
2. Warm-up iterations are executed, recorded, and reported as a separate cold
   series. Discarding them would throw away the only cold-start data we get.
3. A failed query is counted and reported. It is never silently retried into
   the sample, because a retry turns a failure into a slow success and makes a
   struggling database look merely sluggish.
"""

from __future__ import annotations

import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from . import config as cfg
from .adapters.base import Adapter
from .metrics import LatencySummary, ThroughputSummary, summarise


@dataclass
class Series:
    """One workload measured once: warm sample + cold sample + failures."""

    name: str
    warm_ms: list[float] = field(default_factory=list)
    cold_ms: list[float] = field(default_factory=list)
    rows: list[int] = field(default_factory=list)
    errors: int = 0
    timeouts: int = 0
    error_examples: list[str] = field(default_factory=list)

    def summary(self) -> LatencySummary:
        return summarise(
            self.warm_ms, errors=self.errors, timeouts=self.timeouts, rows=self.rows
        )

    def cold_summary(self) -> LatencySummary:
        return summarise(self.cold_ms)


def _timed(fn: Callable[[], list]) -> tuple[float, int]:
    """Run `fn`, return (elapsed_ms, rows_returned)."""
    start = time.perf_counter_ns()
    rows = fn()
    elapsed_ms = (time.perf_counter_ns() - start) / 1e6
    return elapsed_ms, len(rows) if rows is not None else 0


def measure(
    name: str,
    call: Callable[[int], list],
    inputs: list[int],
    iterations: int | None = None,
    warmup: int | None = None,
) -> Series:
    """Run `call(input)` warmup+iterations times, cycling through `inputs`.

    `inputs` is the shared, seed-derived pool (start node ids, or years). The
    same pool in the same order is replayed against every platform, so two
    databases are never asked about different nodes.
    """
    # Resolved at call time, not as default arguments: `bench selftest`
    # shrinks these at runtime and default arguments would be frozen at import.
    iterations = cfg.ITERATIONS if iterations is None else iterations
    warmup = cfg.WARMUP if warmup is None else warmup

    series = Series(name=name)
    total = warmup + iterations
    for i in range(total):
        arg = inputs[i % len(inputs)]
        try:
            elapsed_ms, row_count = _timed(lambda: call(arg))
        except Exception as exc:  # noqa: BLE001 - failures are data
            series.errors += 1
            if len(series.error_examples) < 5:
                series.error_examples.append(f"{type(exc).__name__}: {exc}"[:300])
            continue
        if elapsed_ms > cfg.QUERY_TIMEOUT_S * 1000:
            series.timeouts += 1
            continue
        if i < warmup:
            series.cold_ms.append(elapsed_ms)
        else:
            series.warm_ms.append(elapsed_ms)
            series.rows.append(row_count)
    return series


def measure_nullary(
    name: str,
    call: Callable[[], list],
    iterations: int | None = None,
    warmup: int | None = None,
) -> Series:
    """Same as `measure` for workloads that take no parameter (aggregation)."""
    return measure(name, lambda _: call(), [0], iterations, warmup)


# --------------------------------------------------------------------------
# Mixed read/write workload
# --------------------------------------------------------------------------


@dataclass
class _WorkerTally:
    reads: int = 0
    writes: int = 0
    errors: int = 0
    read_ms: list[float] = field(default_factory=list)
    write_ms: list[float] = field(default_factory=list)


def run_mixed(
    adapter: Adapter,
    start_nodes: list[int],
    concurrency: int,
    duration_s: float | None = None,
    read_ratio: float | None = None,
    seed: int = cfg.SEED,
) -> ThroughputSummary:
    """Sustain a read/write mix at fixed client concurrency for `duration_s`.

    Threads, not processes or asyncio: every driver in this suite is blocking
    and releases the GIL while waiting on the socket, so N threads really do
    keep N requests in flight. The client is not the bottleneck at 40 clients
    against a 0.5-vCPU server -- but `bench doctor` measures that claim
    against the mock backend rather than asserting it.

    Reads are 1-hop traversals (representative of the common case, and cheap
    enough that the server rather than the query plan is the limiter). Writes
    are property updates on existing nodes, so the graph does not grow during
    the run and reads stay comparable across concurrency levels.
    """
    duration_s = cfg.MIXED_DURATION_S if duration_s is None else duration_s
    read_ratio = cfg.READ_RATIO if read_ratio is None else read_ratio

    stop_at = time.perf_counter() + duration_s
    barrier = threading.Barrier(concurrency)
    tallies = [_WorkerTally() for _ in range(concurrency)]

    def worker(idx: int) -> None:
        rng = random.Random(seed + idx)
        tally = tallies[idx]
        # Align the start so that ramp-up is not counted as steady state.
        barrier.wait()
        while time.perf_counter() < stop_at:
            node = rng.choice(start_nodes)
            is_read = rng.random() < read_ratio
            start = time.perf_counter_ns()
            try:
                if is_read:
                    adapter.q_hop(node, 1)
                else:
                    adapter.w_upsert(node, rng.randint(0, 1_000_000))
            except Exception:  # noqa: BLE001
                tally.errors += 1
                continue
            elapsed_ms = (time.perf_counter_ns() - start) / 1e6
            if is_read:
                tally.reads += 1
                tally.read_ms.append(elapsed_ms)
            else:
                tally.writes += 1
                tally.write_ms.append(elapsed_ms)

    wall_start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        list(pool.map(worker, range(concurrency)))
    wall = time.perf_counter() - wall_start

    reads = sum(t.reads for t in tallies)
    writes = sum(t.writes for t in tallies)
    errors = sum(t.errors for t in tallies)
    read_ms = [ms for t in tallies for ms in t.read_ms]
    write_ms = [ms for t in tallies for ms in t.write_ms]

    return ThroughputSummary(
        concurrency=concurrency,
        duration_s=round(wall, 3),
        operations=reads + writes,
        reads=reads,
        writes=writes,
        errors=errors,
        qps=round((reads + writes) / wall, 2) if wall else 0.0,
        read_latency=summarise(read_ms),
        write_latency=summarise(write_ms),
    )
