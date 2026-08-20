# Results matrix

_Generated 2026-08-20 14:00 UTC by `python -m bench report`. 

## 1. Environment and tier parity

| Platform                            | Engine                                      | Query language   | Deployment         | vCPU                   | RAM                    | Disk                                | TCP RTT (median)   |
|-------------------------------------|---------------------------------------------|------------------|--------------------|------------------------|------------------------|-------------------------------------|--------------------|
| CognoDB Cloud (c0 free)             | CognoDB                                     | Cypher (Bolt)    | managed-free-tier  | 0.5 (burstable)        | 256 MB                 | 1 GB                                | 297.41 ms          |
| Neo4j AuraDB Free                   | Neo4j 5.x                                   | Cypher (Bolt)    | managed-free-tier  | not published (shared) | not published (shared) | 200k nodes / 400k relationships cap | 110.28 ms          |
| Memgraph 2.x (self-hosted, capped)  | Memgraph (in-memory, C++)                   | Cypher (Bolt)    | self-hosted-capped | 0.5 (cgroup limit)     | 256 MB (cgroup limit)  | 1 GB volume                         | 0.97 ms            |
| FalkorDB (self-hosted, capped)      | FalkorDB (sparse linear algebra over Redis) | Cypher (RESP)    | self-hosted-capped | 0.5 (cgroup limit)     | 256 MB (cgroup limit)  | 1 GB volume                         | 0.96 ms            |
| ArangoDB 3.11 (self-hosted, capped) | ArangoDB (multi-model, RocksDB)             | AQL (HTTP)       | self-hosted-capped | 0.5 (cgroup limit)     | 256 MB (cgroup limit)  | 1 GB volume                         | 0.98 ms            |

> `TCP RTT (median)` is the floor under every latency below. Self-hosted
> instances have a near-zero RTT and managed instances do not, so subtract
> it before comparing a managed platform against a local container.

## 2. Ingest throughput

| Platform                            | Nodes/s   | Rels/s   | Total load time   | Verified nodes / rels   | Load method                                                                                                              |
|-------------------------------------|-----------|----------|-------------------|-------------------------|--------------------------------------------------------------------------------------------------------------------------|
| CognoDB Cloud (c0 free)             | 2,561     | 5,118    | 21.6 s            | 5,325 / 100,000         | official neo4j Python driver, UNWIND-batched CREATE over Bolt; relationships matched on the unique :Paper(id) constraint |
| Neo4j AuraDB Free                   | 6,403     | 10,592   | 10.3 s            | 5,325 / 100,000         | official neo4j Python driver, UNWIND-batched CREATE over Bolt; relationships matched on the unique :Paper(id) constraint |
| Memgraph 2.x (self-hosted, capped)  | 22,001    | 26,987   | 3.9 s             | 5,325 / 100,000         | official neo4j Python driver, UNWIND-batched CREATE over Bolt; relationships matched on the unique :Paper(id) constraint |
| FalkorDB (self-hosted, capped)      | 18,442    | 25,814   | 4.2 s             | 5,325 / 100,000         | falkordb-py client, UNWIND-batched CREATE over RESP                                                                      |
| ArangoDB 3.11 (self-hosted, capped) | 29,761    | 26,980   | 3.9 s             | 5,325 / 100,000         | python-arango insert_many() bulk document API over HTTP                                                                  |

## 3. Traversal latency (warm)

| Platform                            |   1-hop p50 (ms) |   1-hop p95 (ms) |   2-hop p50 (ms) |   2-hop p95 (ms) |   3-hop p50 (ms) | 3-hop p95 (ms)   |
|-------------------------------------|------------------|------------------|------------------|------------------|------------------|------------------|
| CognoDB Cloud (c0 free)             |           308.31 |           377.7  |           315.1  |           380.17 |           442.05 | 1,080.34         |
| Neo4j AuraDB Free                   |           106.84 |           122.79 |           111.47 |           129.95 |           130.96 | 262.42           |
| Memgraph 2.x (self-hosted, capped)  |             1.66 |             2.8  |             7.4  |            18.1  |            23.43 | 46.76            |
| FalkorDB (self-hosted, capped)      |             1.2  |             2.3  |             2.19 |             4.12 |             5.03 | 8.78             |
| ArangoDB 3.11 (self-hosted, capped) |            44.09 |            48.3  |            46.32 |            49.56 |            62.23 | 97.64            |

## 4. Traversal latency (cold, first 30 iterations)

| Platform                            |   1-hop p50 (ms) |   1-hop p95 (ms) |   2-hop p50 (ms) |   2-hop p95 (ms) |   3-hop p50 (ms) | 3-hop p95 (ms)   |
|-------------------------------------|------------------|------------------|------------------|------------------|------------------|------------------|
| CognoDB Cloud (c0 free)             |           318.99 |           380.15 |           317.81 |           361.65 |           406.97 | 1,064.27         |
| Neo4j AuraDB Free                   |           109.67 |           235.81 |           112.89 |           130.61 |           131.65 | 263.52           |
| Memgraph 2.x (self-hosted, capped)  |             1.92 |             3.09 |             7.33 |            17.35 |            24.53 | 43.65            |
| FalkorDB (self-hosted, capped)      |             1.48 |             2.49 |             2.21 |             4.37 |             5.28 | 8.30             |
| ArangoDB 3.11 (self-hosted, capped) |            44.12 |            48.33 |            46.75 |            49.81 |            62.44 | 91.81            |

## 5. Lookups and aggregation (warm)

| Platform                            |   Point lookup p50 (ms) |   Point lookup p95 (ms) |   Filtered lookup (indexed year) p50 (ms) |   Filtered lookup (indexed year) p95 (ms) |   Aggregation (count group-by year) p50 (ms) |   Aggregation (count group-by year) p95 (ms) |
|-------------------------------------|-------------------------|-------------------------|-------------------------------------------|-------------------------------------------|----------------------------------------------|----------------------------------------------|
| CognoDB Cloud (c0 free)             |                  315.76 |                  372.86 |                                    312.16 |                                    368.7  |                                       318.34 |                                       369.58 |
| Neo4j AuraDB Free                   |                  107.98 |                  119.89 |                                    109.69 |                                    120.65 |                                       109.14 |                                       120.26 |
| Memgraph 2.x (self-hosted, capped)  |                    1.18 |                    1.5  |                                      4.37 |                                      5.53 |                                         3.13 |                                         4.27 |
| FalkorDB (self-hosted, capped)      |                    0.98 |                    1.38 |                                      1.38 |                                      1.96 |                                         1.93 |                                         2.68 |
| ArangoDB 3.11 (self-hosted, capped) |                   44.11 |                   47.79 |                                     44.18 |                                     48.02 |                                        48.08 |                                        49.76 |

### Indexes in place during these measurements

| Platform                            | Index / constraint DDL executed                                                                                                                                                                                  |
|-------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| CognoDB Cloud (c0 free)             | `CREATE CONSTRAINT paper_id IF NOT EXISTS FOR (p:Paper) REQUIRE p.id IS UNIQUE`<br>`CREATE INDEX paper_year IF NOT EXISTS FOR (p:Paper) ON (p.year)`                                                             |
| Neo4j AuraDB Free                   | `CREATE CONSTRAINT paper_id IF NOT EXISTS FOR (p:Paper) REQUIRE p.id IS UNIQUE`<br>`CREATE INDEX paper_year IF NOT EXISTS FOR (p:Paper) ON (p.year)`                                                             |
| Memgraph 2.x (self-hosted, capped)  | `CREATE CONSTRAINT ON (p:Paper) ASSERT p.id IS UNIQUE`<br>`CREATE INDEX ON :Paper(id)`<br>`CREATE INDEX ON :Paper(year)`                                                                                         |
| FalkorDB (self-hosted, capped)      | `CREATE INDEX FOR (p:Paper) ON (p.id)`<br>`CREATE INDEX FOR (p:Paper) ON (p.year)`                                                                                                                               |
| ArangoDB 3.11 (self-hosted, capped) | `CREATE COLLECTION papers (primary index on _key)`<br>`CREATE EDGE COLLECTION cites (_from/_to indexes)`<br>`CREATE PERSISTENT INDEX idx_year ON papers(year)`<br>`CREATE PERSISTENT INDEX idx_id ON papers(id)` |

## 6. Result-set parity check (median rows returned)

| Platform                            |   1-hop |   2-hop |   3-hop |   Filtered |   Aggregation |
|-------------------------------------|---------|---------|---------|------------|---------------|
| CognoDB Cloud (c0 free)             |      16 |   201.5 |     765 |        100 |            12 |
| Neo4j AuraDB Free                   |      16 |   201.5 |     765 |        100 |            12 |
| Memgraph 2.x (self-hosted, capped)  |      16 |   201.5 |     765 |        100 |            12 |
| FalkorDB (self-hosted, capped)      |      16 |   201.5 |     765 |        100 |            12 |
| ArangoDB 3.11 (self-hosted, capped) |      16 |   201.5 |     765 |        100 |            12 |

## 7. Run-to-run variance (coefficient of variation of p50 across repeats)

| Platform                            | 1-hop p50 CV   | 2-hop p50 CV   | 3-hop p50 CV   | Point p50 CV   |
|-------------------------------------|----------------|----------------|----------------|----------------|
| CognoDB Cloud (c0 free)             | 7.62%          | 1.15%          | 3.31%          | 7.12%          |
| Neo4j AuraDB Free                   | 3.26%          | 0.90%          | 1.32%          | 2.15%          |
| Memgraph 2.x (self-hosted, capped)  | 6.94%          | 4.25%          | 0.77%          | 10.66%         |
| FalkorDB (self-hosted, capped)      | 31.75%         | 2.76%          | 3.24%          | 7.44%          |
| ArangoDB 3.11 (self-hosted, capped) | 0.09%          | 0.35%          | 0.71%          | 0.23%          |

## 8. Mixed workload — concurrency sweep

| Platform                            |   Clients |   Sustained QPS |   Read p50 (ms) |   Read p95 (ms) |   Write p50 (ms) |   Write p95 (ms) |   Errors |
|-------------------------------------|-----------|-----------------|-----------------|-----------------|------------------|------------------|----------|
| CognoDB Cloud (c0 free)             |         1 |             3.2 |          319.41 |          359.96 |           318.39 |           349.31 |        0 |
| CognoDB Cloud (c0 free)             |        10 |            31   |          319.23 |          361.19 |           313.18 |           361.24 |        0 |
| CognoDB Cloud (c0 free)             |        40 |           125.5 |          315    |          372.16 |           295.68 |           341.8  |        0 |
| Neo4j AuraDB Free                   |         1 |             9.1 |          109.94 |          120.88 |           109.75 |           138.32 |        0 |
| Neo4j AuraDB Free                   |        10 |            97.4 |          100.11 |          111.23 |           103.79 |           114.2  |        0 |
| Neo4j AuraDB Free                   |        40 |           381.2 |          100.76 |          125.05 |           102.54 |           119.48 |        0 |
| Memgraph 2.x (self-hosted, capped)  |         1 |           615.9 |            1.59 |            2.38 |             1.12 |             1.46 |        0 |
| Memgraph 2.x (self-hosted, capped)  |        10 |           770.4 |           12.32 |           28.94 |             2.78 |             6.44 |        0 |
| Memgraph 2.x (self-hosted, capped)  |        40 |           756.7 |           50.23 |          119.73 |             8.68 |            46.97 |        1 |
| FalkorDB (self-hosted, capped)      |         1 |           869.3 |            1.12 |            1.42 |             0.98 |             1.25 |        0 |
| FalkorDB (self-hosted, capped)      |        10 |           840.4 |            3.25 |           77.11 |             3.34 |            78.42 |        0 |
| FalkorDB (self-hosted, capped)      |        40 |           920.8 |           17.09 |           98.29 |            18.2  |            99.7  |      412 |
| ArangoDB 3.11 (self-hosted, capped) |         1 |            22.5 |           44.09 |           47.93 |            44.04 |            47.59 |        0 |
| ArangoDB 3.11 (self-hosted, capped) |        10 |           216.9 |           45.22 |           50.66 |            45.12 |            50.57 |        1 |
| ArangoDB 3.11 (self-hosted, capped) |        40 |           669.9 |           56.96 |           82.16 |            56.98 |            81.47 |        1 |

## 9. Footprint

| Platform                            | Observable footprint                                                                        | Not observable                |
|-------------------------------------|---------------------------------------------------------------------------------------------|-------------------------------|
| CognoDB Cloud (c0 free)             | advertised=0.5 vCPU burstable / 256 MB RAM / 1 GB disk (console)                            | store_size; components        |
| Neo4j AuraDB Free                   | advertised=shared infrastructure; 200k node / 400k relationship cap                         | store_size; memory            |
| Memgraph 2.x (self-hosted, capped)  | advertised=0.5 vCPU / 256 MB (cgroup)                                                       | -                             |
| FalkorDB (self-hosted, capped)      | advertised=0.5 vCPU / 256 MB (cgroup); used_memory_human=14.43M; used_memory_bytes=15131464 | -                             |
| ArangoDB 3.11 (self-hosted, capped) | advertised=0.5 vCPU / 256 MB (cgroup)                                                       | papers_figures; cites_figures |

## 10. Caveats recorded by the harness

**CognoDB Cloud (c0 free)**
- No errors, timeouts or load mismatches recorded.

**Neo4j AuraDB Free**
- No errors, timeouts or load mismatches recorded.

**Memgraph 2.x (self-hosted, capped)**
- mixed @ 40 clients: 1 errors

**FalkorDB (self-hosted, capped)**
- mixed @ 40 clients: 412 errors

**ArangoDB 3.11 (self-hosted, capped)**
- mixed @ 10 clients: 1 errors
- mixed @ 40 clients: 1 errors

