# Methodology

The short version: same data, same queries, same client, same region, same
resource budget, warm-up separated from steady state, percentiles not
averages, and every failure recorded.

The long version is below, organised as the decisions I had to make and why I
made them the way I did. Most of these could reasonably have gone the other
way; what matters is that they went the same way for every platform.

---

## 1. Resource parity

The CognoDB free tier (`c0`) is the smallest instance in the comparison:
**0.5 burstable vCPU, 256 MB RAM, 1 GB disk**. Everything else is capped to
match it, because comparing a 256 MB instance against an 8 GB one measures the
size of the wallet, not the database.

| Platform | How parity was achieved |
|---|---|
| CognoDB Cloud | The reference. Free `c0` tier as provisioned. |
| Neo4j AuraDB Free | **Cannot be capped.** See below. |
| Memgraph | `docker --cpus=0.5 --memory=256m --memory-swap=256m` |
| FalkorDB | same cgroup caps, plus `--maxmemory 230mb` |
| ArangoDB | same cgroup caps, plus RocksDB cache flags |

`--memory-swap` equal to `--memory` matters more than it looks. Docker's
`--memory` on its own still permits swapping, so a container "limited to
256 MB" can be running in 256 MB of RAM plus however much swap the host has.
Setting them equal disables swap and makes the cap real. You can confirm it:

```bash
docker stats --no-stream
cat /sys/fs/cgroup/memory.max      # cgroup v2, inside the container
```

**The Aura Free exception.** Neo4j does not publish CPU or RAM figures for
Aura Free and gives the user no way to constrain them; the tier is defined by
a 200k node / 400k relationship cap instead. This is the one genuine parity
gap in the suite and it cannot be closed from the client side. Two things
follow: (a) it is stated next to every Aura number rather than in a footnote,
and (b) if Aura wins a workload, the honest conclusion is "Aura Free was
faster on unknown hardware", not "Neo4j is faster than X".

I considered dropping Aura for a self-hosted Neo4j capped to 256 MB. I kept
the managed tier because a benchmark of *managed graph database platforms*
that excludes the most widely used managed graph database would be a strange
document, and because the self-hosted comparison is already covered — Memgraph
and FalkorDB are Cypher engines running under exactly the cap.

---

## 2. The network is part of the benchmark

Self-hosted containers on the client machine have a sub-millisecond round trip.
A managed instance in another availability zone does not. If you benchmark
local Docker containers against a remote managed service from your laptop, you
have measured your Wi-Fi.

Two mitigations, both cheap:

1. **Run the client on a cloud VM in the same region as the managed
   instances**, with the self-hosted containers on that same VM. This keeps
   every path short.
2. **Measure the floor and publish it.** Before each run the harness opens 20
   TCP connections to the endpoint and records the median connect time. That
   number appears in the environment table in RESULTS.md. Subtract it before
   comparing a managed platform against a local container.

The RTT probe is a TCP handshake, not a TLS handshake, so for the `bolt+s://`
endpoints it under-measures the true per-connection cost. It is a floor, not
an estimate — which is exactly how it should be read.

---

## 3. Dataset

SNAP `cit-HepTh`, the arXiv high-energy-physics citation network, snowball-
sampled to 200,000 relationships with seed 42.

Why this one:

- It is genuinely directed and genuinely skewed. Citation networks have
  hub papers cited hundreds of times, so 2- and 3-hop traversals fan out the
  way they do in production graphs. A synthetic uniform-degree graph would
  make every engine look good.
- It ships a companion file of publication dates, which gives a **real**
  property to index and group by rather than a synthetic `rand()` column.
- It is small enough to download in seconds and to fit in 256 MB.

Why snowball sampling and not a random edge sample: a uniformly random subset
of edges destroys local clustering, so multi-hop queries stop fanning out and
the traversal benchmark stops measuring traversal. Snowballing outward from a
fixed high-degree seed node preserves the dense neighbourhood structure.

The prepared CSVs are sha256-hashed and the hashes are written into every
result file. The reporter refuses to publish a comparison where two platforms
were loaded from different hashes.

**Caveat, recorded honestly:** roughly 1–2% of `cit-HepTh` nodes have no date
in the companion file. Those get a deterministic pseudo-random year in
1992–2003. This affects the *selectivity* of the filtered lookup very slightly
and affects it identically on every platform.

---

## 4. Warm-up and cold starts

Every read workload runs `WARMUP` (30) iterations before `ITERATIONS` (300)
measured ones. The warm-up iterations are not discarded — they are recorded as
a separate **cold** series and reported in their own table. It costs nothing
and it is the only cold-start data the run produces.

Cold numbers are noisier by nature (connection pool fill, plan cache misses,
page cache misses, and on some free tiers, instance wake-up). They are
reported next to the warm numbers, never blended into them.

---

## 5. Iterations, repeats and variance

- 300 measured iterations per read workload (the assignment asks for ≥ 100).
- The whole read suite runs 3 times. Reported percentiles are pooled across
  all three (so ~900 observations), and the **spread of the per-repeat p50 and
  p95** is reported as a coefficient of variation.

That CV column is the most useful thing in the results matrix. On a burstable
0.5-vCPU instance, run-to-run variance of 10–20% is entirely normal, and if
platform A's p50 is 5% below platform B's while both have a 15% CV, the
correct reading is "no measured difference". Without the variance column a
reader has no way to know that.

- Start nodes: 200 drawn once with seed 42 from the set of nodes that have at
  least one outgoing edge, frozen to `results/shared_inputs.json`, and replayed
  in the same order against every platform. Nodes with zero out-degree are
  excluded because they make traversals trivially empty.

---

## 6. Timing

`time.perf_counter_ns()` around the driver call, client-side, including
serialisation and the full round trip. Server-reported execution time is
deliberately not used: it excludes exactly the costs a user of a managed cloud
database actually pays.

Every adapter method fully materialises its result before returning. This is
the single most common way a graph benchmark gets accidentally faked — most
drivers return a lazy cursor, and if you stop the clock before draining it you
have timed a request header.

Percentiles are **nearest-rank**, no interpolation: the reported p95 is a
latency that actually occurred. `numpy.percentile`'s default would interpolate
between neighbouring samples and produce a number no query ever achieved.

---

## 7. Failures

A query that raises is counted as an error and excluded from the latency
sample. It is never retried into the sample, because a retry converts a
failure into a slow success and makes a struggling database look merely slow.
Errors, timeouts, and up to five verbatim exception messages per workload are
carried into the results JSON and printed in the RESULTS.md caveats section.

On 256 MB instances, 3-hop traversals from high-degree start nodes are the
most likely thing to fail. If a platform errors on a workload, the correct
reading of its p95 for that workload is "p95 of the queries that survived" —
which is why the error count sits in the same table.

---

## 8. Mixed workload

90% reads (1-hop traversal) / 10% writes (property update on an existing
node), sustained for 60 seconds at 1, 10 and 40 concurrent clients.

- **Threads, not asyncio.** Every driver here is blocking and releases the GIL
  on socket wait, so N threads really do keep N requests in flight against the
  server. At 40 clients against a 0.5-vCPU server the client is nowhere near
  saturated.
- **Writes are updates, not inserts**, so the graph does not grow during the
  run and reads stay comparable across concurrency levels.
- **A barrier aligns thread start** so ramp-up is not counted as steady state.
- Throughput is computed over the wall-clock duration actually observed, not
  the requested duration.

The interesting output is not the peak QPS. It is the shape: whether a
platform's throughput keeps climbing from 1 to 40 clients or flattens, and
what its p95 does while that happens. A database that doubles throughput and
decuples tail latency has queued, not scaled.

---

## 9. What this benchmark does not measure

Stated up front, because a benchmark's scope is part of its honesty:

- **Anything above 256 MB.** These are entry-tier results. They say nothing
  about how any of these engines behave with a real working set in RAM.
- **Write-heavy or transactional workloads** beyond the 10% update mix.
- **Graph algorithms** (PageRank, community detection, shortest path over the
  whole graph).
- **Durability, failover, backup/restore, or consistency under partition.**
- **Cost.** Every tier here is free, which makes price-performance undefined.
- **Operability** — dashboards, alerting, migration tooling. For a production
  choice this often matters more than a p95.
