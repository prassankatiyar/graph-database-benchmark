# I gave five graph databases 256 MB each. Three of them I failed to measure at all.

*What happens when you take a benchmark's fairness rules literally.*

---

Every database benchmark you have ever read was won by the database that
commissioned it.

That is not a conspiracy. It is just what happens when the person choosing the
dataset, the queries, the hardware and the tuning is the same person who wants
a particular answer. There are a hundred honest-looking decisions in a
benchmark, and if you make all hundred in the same direction you get whatever
result you like without ever writing down a false number.

So when I set out to benchmark **CognoDB Cloud** against four other graph
databases, I decided the deliverable wasn't the leaderboard. It was the
methodology â€” every decision written down, every escape hatch closed, and every
place I failed to close one flagged in the results table where nobody could
miss it.

Then the constraints started breaking things, and the broken things turned out
to be the interesting part.

**Code and full results: [github.com/prassankatiyar/graph-database-benchmark](https://github.com/prassankatiyar/graph-database-benchmark)**

---

## The rule: nobody gets better hardware

CognoDB's free tier is deliberately small â€” **0.5 burstable vCPU, 256 MB RAM,
1 GB disk.**

The tempting move is to compare it against whatever free tier the other vendors
happen to offer, which in practice means putting a 256 MB instance against
something with 8 GB. That benchmark measures the size of the free tier, not the
quality of the database.

So the smallest instance set the budget for everyone:

```yaml
cpus: "0.5"
mem_limit: 256m
memswap_limit: 256m     # â† this line is the one people forget
```

That third line matters more than it looks. Docker's `--memory` on its own
still permits swapping. Set it alone and your carefully "256 MB" database is
running in 256 MB of RAM *plus as much swap as the host will give it* â€” a
completely different machine that happens to report the number you wanted.
Setting `memswap_limit` equal to `mem_limit` disables swap and makes the cap
mean what it says.

The lineup, chosen so each one isolates a single variable:

| Platform | Engine | Why it's here |
|---|---|---|
| **CognoDB Cloud** | Cypher over Bolt | The subject |
| **Neo4j AuraDB Free** | Neo4j 5.x | Same protocol, reference implementation |
| **Memgraph** | In-memory C++ | Same language, different storage model |
| **FalkorDB** | Sparse matrices over Redis | Same language, radically different execution |
| **ArangoDB** | Multi-model, RocksDB | The control: not Cypher at all |

**The one gap I couldn't close:** Neo4j publishes no CPU or RAM for Aura Free
and gives you no way to constrain them. There is no client-side trick that
fixes that. So rather than quietly benchmark against unknown hardware and hope
nobody asked, it's stated in the environment table, in the caveats, in the
methodology doc, and beside every Aura number. If Aura wins something, the
strongest honest claim is *"Aura Free was faster on undisclosed hardware."*

A caveat you volunteer is credibility. A caveat someone finds is a retraction.

---

## Then the constraints started breaking things

I expected the resource cap to make the numbers *smaller*. It mostly made them
**absent**, and each failure said something a feature comparison never would.

**ArangoDB wouldn't boot.** The log showed why: `memory limit per AQL query
automatically set to 4795133952 bytes`. It had sized its query budget at 4.8 GB
â€” 75% of my *laptop's* RAM â€” while living in a 256 MB container. ArangoDB reads
host memory, not its cgroup. Anyone containerising it with a memory cap is
running defaults calibrated for a machine sixty times larger than the one they
actually have.

**Memgraph hit a wall at 200,000 relationships:**

```
Memory limit exceeded! Attempting to allocate a chunk of 644.00KiB which would
put the current use to 230.02MiB, while the maximum allowed size for
allocation is set to 230.00MiB.
```

It holds the whole graph in RAM with nowhere to spill. The disk-backed engines
took the same load without complaint. Since the benchmark requires one
identical dataset everywhere, **the most memory-constrained engine set the size
for all of them** â€” I dropped to 100,000 relationships and reloaded every
platform. That is the fairness rule working as designed, and the opposite of
tuning the dataset until a favourite looks good.

**And my own parity check caught me cheating by accident.** The results matrix
includes a table of *how many rows each platform returned per query* â€” because
if one database returns 601 rows for a 3-hop traversal and another returns 765,
they were not asked the same question and no amount of percentile hygiene fixes
it.

ArangoDB returned 601. Everyone else returned 765.

My AQL translation used `uniqueVertices: 'global'`, which forbids revisiting a
vertex anywhere in the traversal â€” so any node reachable at a shallower depth
silently vanished from the deeper result. Cypher's `[:CITES*3..3]` keeps it. One
word changed, and ArangoDB started doing 21% more work.

If you build one benchmark table in your life, build that one.

---

## The punchline: I only measured two of the five

Here is the number that reframed the whole project. A point lookup is the
cheapest query in the suite â€” one indexed fetch of one node. Compare it against
the time to merely open a TCP connection to the same host:

| Platform | TCP round trip | Point lookup p50 | Query time left over |
|---|---|---|---|
| FalkorDB | 0.96 ms | 0.98 ms | ~0 ms |
| Memgraph | 0.97 ms | 1.18 ms | ~0.2 ms |
| ArangoDB | 0.98 ms | 44.11 ms | ~43 ms of *fixed* overhead |
| Neo4j Aura Free | 110.28 ms | 107.98 ms | **negative** |
| CognoDB Cloud | 297.41 ms | 315.76 ms | ~18 ms |

On Aura, the query finished faster than a handshake. That says nothing about
Neo4j â€” it says a fixed 110 ms of wire swallowed the entire measurement. Same
for CognoDB at 297 ms. And ArangoDB posted 44 ms on *every* workload regardless
of complexity: point lookup 44.11, filtered lookup 44.18, 1-hop 44.09. A
single-document fetch and a twelve-group aggregation cannot cost the same
amount, so that is a fixed cost, not query work.

Two clues confirm it. ArangoDB sustained 22.5 queries/second at one client â€”
and 1 Ã· 0.0445 s is exactly 22.5. And its run-to-run variance was **0.09%**,
the lowest in the table by an order of magnitude. A system doing real work has
variance. A system waiting on a constant does not.

So three of five platforms were measured through a transport that dominated the
signal. **I could have published the raw table and let "CognoDB: 308 ms,
Memgraph: 1.66 ms" speak for itself.** It would have looked authoritative and
been meaningless.

What survives is the *excess* over each platform's own floor â€” every platform
pays its fixed cost on the point lookup too, so subtracting it isolates the work
the query actually caused. All five returned an identical 765 rows:

| Platform | 3-hop p50 | minus its own floor | **Traversal cost** |
|---|---|---|---|
| FalkorDB | 5.03 ms | 0.98 ms | **4.05 ms** |
| ArangoDB | 62.23 ms | 44.11 ms | **18.12 ms** |
| Memgraph | 23.43 ms | 1.18 ms | **22.25 ms** |
| Neo4j Aura Free | 130.96 ms | 107.98 ms | **22.98 ms** |
| CognoDB Cloud | 442.05 ms | 315.76 ms | **126.29 ms** |

---

## The best finding was hiding in the footprint table

FalkorDB held the entire 5,325-node, 100,000-relationship graph in **14.43 MB**.
About 140 bytes per relationship.

Memgraph, on the identical budget, couldn't fit 200,000 relationships at all â€”
which puts it somewhere north of 1 KB per relationship. Close to **an order of
magnitude less compact** for the same graph.

That single fact explains both halves of the story. FalkorDB stores the graph
as sparse adjacency matrices and evaluates an *n*-hop traversal as repeated
sparse matrixâ€“vector multiplication. Memgraph stores vertex and edge objects
and walks pointers between them. The matrix form is far denser â€” and density is
*why* it's fast: at a frontier of a few hundred vertices the working set stays
in cache while a pointer walk chases scattered allocations.

FalkorDB's 5Ã— traversal advantage and Memgraph's inability to hold the original
dataset are the same architectural choice, seen from two directions.
**"In-memory" is a performance property right up until it becomes a capacity
limit** â€” and on an entry tier, that arrives early.

---

## Two charts that mean the opposite of what they look like

| Platform | 1 client | 10 clients | 40 clients | read p95, 1 â†’ 40 |
|---|---|---|---|---|
| FalkorDB | 869.3 | 840.4 | 920.8 | 1.42 â†’ 98.29 ms |
| Memgraph | 615.9 | 770.4 | 756.7 | 2.38 â†’ 119.73 ms |
| ArangoDB | 22.5 | 216.9 | 669.9 | 47.93 â†’ 82.16 ms |
| Aura | 9.1 | 97.4 | 381.2 | 120.88 â†’ **125.05 ms** |
| CognoDB | 3.2 | 31.0 | 125.5 | 359.96 â†’ **372.16 ms** |

Aura goes 9.1 â†’ 97.4 â†’ 381.2 queries/second. Beautiful linear scaling. And its
tail latency *does not move* â€” 120.9 ms to 125.0 ms across a 40Ã— increase in
load.

That is not scaling. That is Little's Law. Throughput = concurrency Ã· latency,
and when latency is a network constant that concurrency can't perturb,
throughput rises in exact proportion to client count. **A linear throughput
curve with a flat p95 is the signature of a server that was never the
bottleneck.** Those instances were idling. The wire was full.

Meanwhile Memgraph gains 25% from 1 to 10 clients, then *loses* ground at 40,
while its p95 climbs 2.4 â†’ 28.9 â†’ 119.7 ms. A 50Ã— increase. That is a real
server saturating: forty clients queueing for half a vCPU.

**The two platforms that look worst on the throughput chart are the only two
actually being stressed.**

And FalkorDB's 5Ã— speed advantage came with a bill. At 40 clients it failed
**412 requests**. Memgraph, identical workload, identical hardware, failed
**one**. Its tail also splits 24Ã— at just 10 clients â€” p50 of 3.25 ms against a
p95 of 77.11 ms â€” which looks like writes serialising against readers rather
than gradual queueing.

A 5Ã— latency win with a 400Ã— error rate is not a 5Ã— win. Which of those two
engines you'd deploy depends entirely on whether your traffic looks like the
1-client column or the 40-client one.

---

## What I'd tell you to take from this

- **Time the query, not the cursor.** Most drivers hand back a lazy cursor. Stop
  the clock before draining it and you've timed a request header. It's a
  one-line difference that moves results by orders of magnitude.
- **Count your rows before you compare your latencies.** It's the cheapest
  possible check and it caught a real bug in my own code.
- **Measure your network floor and publish it.** Otherwise your latency table is
  a map of physical distance wearing a database's name.
- **Report variance, not just percentiles.** On burstable CPU, run-to-run
  spread routinely exceeds the difference between platforms. Without it you'll
  publish noise with a headline on it.
- **Constrain the resources and see what breaks.** Every genuinely interesting
  thing here came from something failing under the cap, not from a number being
  slightly larger than another number.

The honest headline for most of these workloads is that they're within noise of
each other at this tier â€” and the places where a platform genuinely separates
are the only places worth a paragraph.

---

## Run it yourself and disagree with me

Free-tier accounts, Docker, and:

```bash
make install
make dataset
make doctor      # verifies every connection before you commit 45 minutes
make bench
make report
```

Every run writes a self-describing JSON file carrying the dataset hash, the git
SHA, the host fingerprint, the index DDL that actually executed, the measured
network RTT, and every caveat the run produced. Six months from now, any number
in the tables traces back to the exact conditions that produced it.

If you find somewhere I've been unfair to one of these databases, open an issue.
That's not politeness â€” a benchmark that can't be falsified isn't evidence, it's
marketing.

