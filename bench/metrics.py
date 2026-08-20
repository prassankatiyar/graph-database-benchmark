"""Latency and throughput statistics.

Percentiles use the nearest-rank definition (NIST / ISO "method 1"): the p-th
percentile is the value at index ceil(p/100 * n) - 1 of the sorted sample. No
interpolation. This matters more than it sounds: numpy's default percentile
interpolates between neighbouring samples, which produces a p95 that never
actually occurred, and different tools then disagree about the same data. With
n >= 300 the difference is small, but "small" is not the same as "auditable".
"""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass, field


def percentile(sorted_values: list[float], p: float) -> float:
    """Nearest-rank percentile of an already-sorted, non-empty list."""
    if not sorted_values:
        raise ValueError("percentile() of an empty sample")
    if p <= 0:
        return sorted_values[0]
    rank = math.ceil(p / 100.0 * len(sorted_values))
    return sorted_values[min(rank, len(sorted_values)) - 1]


@dataclass
class LatencySummary:
    """Summary of one series of per-query latencies, in milliseconds."""

    n: int
    p50: float
    p90: float
    p95: float
    p99: float
    mean: float
    stdev: float
    min: float
    max: float
    errors: int = 0
    timeouts: int = 0
    rows_returned_median: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def summarise(
    latencies_ms: list[float],
    *,
    errors: int = 0,
    timeouts: int = 0,
    rows: list[int] | None = None,
) -> LatencySummary:
    """Turn raw per-query latencies into a reportable summary."""
    if not latencies_ms:
        return LatencySummary(
            n=0,
            p50=float("nan"),
            p90=float("nan"),
            p95=float("nan"),
            p99=float("nan"),
            mean=float("nan"),
            stdev=float("nan"),
            min=float("nan"),
            max=float("nan"),
            errors=errors,
            timeouts=timeouts,
        )
    ordered = sorted(latencies_ms)
    return LatencySummary(
        n=len(ordered),
        p50=round(percentile(ordered, 50), 3),
        p90=round(percentile(ordered, 90), 3),
        p95=round(percentile(ordered, 95), 3),
        p99=round(percentile(ordered, 99), 3),
        mean=round(statistics.fmean(ordered), 3),
        stdev=round(statistics.stdev(ordered), 3) if len(ordered) > 1 else 0.0,
        min=round(ordered[0], 3),
        max=round(ordered[-1], 3),
        errors=errors,
        timeouts=timeouts,
        rows_returned_median=(
            round(statistics.median(rows), 1) if rows else 0.0
        ),
    )


@dataclass
class RepeatVariance:
    """Spread of a single statistic across independent repeats of a suite.

    Reported so that a 3% difference between two platforms can be recognised
    as noise rather than sold as a result.
    """

    values: list[float] = field(default_factory=list)

    @property
    def mean(self) -> float:
        return round(statistics.fmean(self.values), 3) if self.values else float("nan")

    @property
    def stdev(self) -> float:
        return (
            round(statistics.stdev(self.values), 3) if len(self.values) > 1 else 0.0
        )

    @property
    def cv_percent(self) -> float:
        """Coefficient of variation: stdev as a percentage of the mean."""
        if not self.values or self.mean == 0:
            return float("nan")
        return round(self.stdev / self.mean * 100, 2)

    def to_dict(self) -> dict:
        return {
            "values": [round(v, 3) for v in self.values],
            "mean": self.mean,
            "stdev": self.stdev,
            "cv_percent": self.cv_percent,
        }


@dataclass
class ThroughputSummary:
    """Result of a sustained, fixed-duration, fixed-concurrency run."""

    concurrency: int
    duration_s: float
    operations: int
    reads: int
    writes: int
    errors: int
    qps: float
    read_latency: LatencySummary
    write_latency: LatencySummary

    def to_dict(self) -> dict:
        d = asdict(self)
        d["read_latency"] = self.read_latency.to_dict()
        d["write_latency"] = self.write_latency.to_dict()
        return d
