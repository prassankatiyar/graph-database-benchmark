"""Tests for the parts of the harness that would silently corrupt results.

I did not try to test the adapters against real databases -- that is what
`bench doctor` and the self-test are for. What is tested here is the maths and
the sampling, because a wrong percentile or a biased sample produces numbers
that look completely plausible and are completely wrong.
"""

from __future__ import annotations

import pytest

from bench.dataset import snowball
from bench.metrics import percentile, summarise
from bench.workloads import measure


class TestPercentile:
    def test_nearest_rank_matches_worked_example(self):

        data = [15, 20, 35, 40, 50]
        data.sort()
        assert percentile(data, 30) == 20
        assert percentile(data, 40) == 20
        assert percentile(data, 50) == 35
        assert percentile(data, 100) == 50

    def test_never_invents_a_value(self):
        """A nearest-rank percentile must be a value that actually occurred."""
        data = sorted([1.0, 2.0, 3.0, 100.0])
        for p in (1, 25, 50, 75, 95, 99, 100):
            assert percentile(data, p) in data

    def test_p95_of_a_hundred_samples_is_the_95th(self):
        data = [float(i) for i in range(1, 101)]
        assert percentile(data, 95) == 95.0
        assert percentile(data, 50) == 50.0

    def test_empty_sample_raises(self):
        with pytest.raises(ValueError):
            percentile([], 50)


class TestSummarise:
    def test_summary_of_empty_series_is_nan_not_zero(self):
        """Zero would silently read as 'infinitely fast' in the results table."""
        s = summarise([], errors=5)
        assert s.n == 0
        assert s.errors == 5
        assert s.p50 != s.p50  # NaN

    def test_ordering_of_percentiles(self):
        s = summarise([float(i) for i in range(1, 1001)])
        assert s.min <= s.p50 <= s.p90 <= s.p95 <= s.p99 <= s.max

    def test_errors_are_not_counted_as_observations(self):
        s = summarise([1.0, 2.0, 3.0], errors=7, timeouts=2)
        assert s.n == 3
        assert s.errors == 7
        assert s.timeouts == 2


class TestSnowball:
    def _ring_with_hubs(self, n=500):
        edges = [(i, (i + 1) % n) for i in range(n)]
        edges += [(i, 0) for i in range(0, n, 3)]  # node 0 is a hub
        return edges

    def test_is_deterministic(self):
        edges = self._ring_with_hubs()
        a = snowball(edges, 200, seed=42)
        b = snowball(edges, 200, seed=42)
        assert a == b

    def test_respects_target_size(self):
        edges = self._ring_with_hubs()
        _, kept = snowball(edges, 200, seed=42)
        assert len(kept) <= 200

    def test_returns_induced_subgraph_only(self):
        """Every kept edge must have both endpoints in the node list."""
        edges = self._ring_with_hubs()
        nodes, kept = snowball(edges, 200, seed=42)
        node_set = set(nodes)
        assert all(src in node_set and dst in node_set for src, dst in kept)

    def test_node_list_has_no_duplicates(self):
        edges = self._ring_with_hubs()
        nodes, _ = snowball(edges, 200, seed=42)
        assert len(nodes) == len(set(nodes))


class TestMeasureLoop:
    def test_splits_cold_and_warm(self):
        series = measure("t", lambda _: [1], [0], iterations=10, warmup=4)
        assert len(series.cold_ms) == 4
        assert len(series.warm_ms) == 10

    def test_cycles_through_the_input_pool(self):
        seen = []

        def call(arg):
            seen.append(arg)
            return []

        measure("t", call, [1, 2, 3], iterations=6, warmup=0)
        assert seen == [1, 2, 3, 1, 2, 3]

    def test_failures_are_counted_not_timed(self):
        def boom(_):
            raise RuntimeError("connection reset")

        series = measure("t", boom, [0], iterations=5, warmup=1)
        assert series.errors == 6
        assert series.warm_ms == []
        assert "connection reset" in series.error_examples[0]

    def test_row_counts_are_recorded(self):
        series = measure("t", lambda _: [1, 2, 3], [0], iterations=5, warmup=0)
        assert series.rows == [3] * 5
        assert series.summary().rows_returned_median == 3.0
