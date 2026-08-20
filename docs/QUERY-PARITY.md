# Query parity

Four of the five platforms speak Cypher, one speaks AQL. Whenever a benchmark
spans query languages, the honest question is not "which database is faster"
but "did you ask them the same question". This file exists so that anyone can
check.

Every query below is reproduced verbatim from the source. If you change a
query, change it here too — or better, delete this file and generate it, which
is what I would do if the suite grew past five platforms.

## The rule I applied

When a dialect could not express the Cypher form exactly, I picked the
**closest semantic equivalent**, not the fastest available equivalent. Where
that choice could plausibly favour one engine, it is listed under "Known
divergences" at the bottom and repeated in the README caveats.

---

## 1-hop / 2-hop / 3-hop traversal

Ask for: the set of distinct nodes reachable from a given start node by
following exactly *N* outgoing `CITES` edges.

**Cypher** (CognoDB, Aura, Memgraph, FalkorDB)

```cypher
MATCH (a:Paper {id: $id})-[:CITES*N..N]->(b:Paper)
RETURN DISTINCT b.id AS id
```

**AQL** (ArangoDB)

```aql
FOR v IN N..N OUTBOUND @start cites
  OPTIONS { uniqueVertices: 'global', bfs: true }
  RETURN DISTINCT v.id
```

`uniqueVertices: 'global'` plus `bfs: true` is the documented way to get
"distinct reachable vertices" in AQL. Without `bfs: true`, `uniqueVertices:
'global'` is not permitted.

The harness records the median number of rows each platform returned for this
query (RESULTS.md §6). If those medians do not match across platforms, the
translation is wrong and the latencies are not comparable — that table is the
check, not decoration.

---

## Point lookup

Ask for: one node by primary identifier.

**Cypher**

```cypher
MATCH (p:Paper {id: $id}) RETURN p.id AS id, p.year AS year, p.title AS title
```

**AQL**

```aql
FOR p IN papers FILTER p._key == @key RETURN { id: p.id, year: p.year, title: p.title }
```

In ArangoDB the node id is stored as `_key`, so this hits the primary index —
the same class of access as a uniqueness-constrained lookup in the Cypher
engines. A separate persistent index on `id` also exists so the two
representations stay in sync.

---

## Filtered lookup (indexed secondary property)

Ask for: up to 100 node ids with a given publication year.

**Cypher**

```cypher
MATCH (p:Paper) WHERE p.year = $year RETURN p.id AS id LIMIT $limit
```

**AQL**

```aql
FOR p IN papers FILTER p.year == @year LIMIT @limit RETURN p.id
```

---

## Aggregation

Ask for: paper count per publication year, ordered by year.

**Cypher**

```cypher
MATCH (p:Paper) RETURN p.year AS year, count(*) AS papers ORDER BY year
```

**AQL**

```aql
FOR p IN papers COLLECT year = p.year WITH COUNT INTO papers SORT year
  RETURN { year, papers }
```

This is deliberately a full scan on every platform. It is the one workload
where nothing can be answered from an index alone, which makes it the closest
thing in the suite to a raw storage-scan benchmark.

---

## Write (mixed workload)

Ask for: set a property on one existing node.

**Cypher**

```cypher
MATCH (p:Paper {id: $id}) SET p.touched = $marker
```

**AQL**

```aql
UPDATE @key WITH { touched: @marker } IN papers
```

Property update rather than insert, on purpose: a 60-second insert-heavy run
at 40 clients would grow the graph by a different amount on each platform, and
every read measured after that point would be against a different dataset.

---

## Indexes created on each platform

The exact DDL executed is recorded in every results JSON under
`schema_applied`, so the README table is generated from what actually ran
rather than from what I meant to run.

| Platform | Statements |
|---|---|
| CognoDB, Aura | `CREATE CONSTRAINT paper_id ... REQUIRE p.id IS UNIQUE`; `CREATE INDEX paper_year FOR (p:Paper) ON (p.year)` |
| Memgraph | `CREATE CONSTRAINT ON (p:Paper) ASSERT p.id IS UNIQUE`; `CREATE INDEX ON :Paper(id)`; `CREATE INDEX ON :Paper(year)` |
| FalkorDB | `CREATE INDEX FOR (p:Paper) ON (p.id)`; `CREATE INDEX FOR (p:Paper) ON (p.year)` |
| ArangoDB | primary index on `_key` (implicit); persistent index on `id`; persistent index on `year`; `_from`/`_to` edge indexes (implicit) |

---

## Known divergences

These are the places where the platforms are genuinely not doing identical
work. They are listed here and in the README rather than buried.

1. **Uniqueness enforcement.** Neo4j-family engines enforce a real uniqueness
   constraint on `Paper.id`; FalkorDB gets a plain index. Constraint checking
   costs write throughput, so this slightly disadvantages the constrained
   engines on ingest and slightly advantages them on lookup. I kept it because
   removing the constraint would have meant no guarantee that the loaded graph
   was correct, and a fast load of a wrong graph is not a result.

2. **Parameterised `LIMIT`.** FalkorDB does not accept a bound parameter in
   `LIMIT`, so the value is interpolated into the query string. The limit is a
   constant (100) throughout, so the work is identical, but FalkorDB sees one
   fixed query string where the others see one parameterised plan. If anything
   this helps FalkorDB's plan cache marginally.

3. **Document vs. native graph storage.** ArangoDB stores nodes as JSON
   documents with a primary index on `_key`. Its point lookup is therefore a
   document fetch, not an index-free adjacency hop. That is not a flaw in the
   translation — it is the actual architectural difference the benchmark is
   trying to expose.

4. **Database selector.** Memgraph Community has no multi-database concept, so
   the Bolt adapter passes `database=None` for it and a named database for
   CognoDB and Aura. This changes one field in the Bolt handshake and nothing
   about the query.
