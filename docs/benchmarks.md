# Benchmarks

`backend/benchmarks/control_plane.py` measures the **control plane**: the API, the ledger and the queue, against a real SQLite
file in a temporary directory (it never touches `~/.patchquest`). Results are stored with git sha and hardware in
`backend/benchmarks/history/`.

```bash
python -m benchmarks.control_plane --orgs 1000 --users-per-org 5 --runs 10000 --events-per-run 40 --out benchmarks/history/
```

## Result (`control-plane-b2cbaae-20261003T041642.json`)

Hardware: Intel Core Ultra 7 255HX (20 cores), 30.8 GB RAM, Linux 7.0, Python 3.12.3.
Data: 1,000 organisations, 5,000 users, 10,000 runs, 400,000 events; one "hot" tenant owns 3,340 runs (33%). Database 98.8 MB.

| API call (in-process, 400 samples each) | typical tenant p50 / p95 | hot tenant (3,340 runs) p50 / p95 |
|---|---|---|
| list runs (50) | 2.5 / 5.5 ms | 2.7 / 3.9 ms |
| get run | 3.7 / 7.3 ms | 2.4 / 3.3 ms |
| events page | 3.8 / 8.1 ms | 3.2 / 4.5 ms |
| pending approvals | 3.7 / 8.1 ms | 4.0 / 5.5 ms |
| authenticated request (no work) | 2.1 / 4.4 ms | 2.1 / 3.0 ms |
| **metrics, 7-day window** | 2.8 / 5.9 ms | **29.5 / 43.9 ms** (max 166 ms) |

| Queue (SQLite, `BEGIN IMMEDIATE`) | claims/s | claim latency p50 / p95 | duplicate claims |
|---|---|---|---|
| 1 claimer, 1,000 runs | 755 | 1.3 / 1.6 ms | 0 |
| 8 concurrent claimers, 1,000 runs | 2,213 | 1.5 / 9.5 ms (max 232 ms) | 0 |

| Ledger append | events/s | latency p50 / p95 |
|---|---|---|
| 1 writer | 699 | 1.4 / 1.7 ms |
| 8 concurrent writers | 1,380 | 5.7 / 6.9 ms |

Process: +33 MB RSS, 34 s CPU for the whole run including seeding.

## How to read this

- **It shows** the application's own cost per request scales with a tenant's data, not the whole database (per-workspace
  indexes), that claims are exclusive under contention, and where the first cost curve is: `metrics` loads every run in the
  window (O(n); 30 ms at 3,300 runs; it would need pre-aggregation well before ~100,000 runs per tenant).
- **It does not show** network transport, TLS, a browser, model latency, agent execution time, many *machines*, or
  PostgreSQL. API numbers are in-process (no sockets): a lower bound for a deployment.
- SQLite serialises writers: ~700-1,400 durable appends/s is the ceiling of one database file. A run emits tens to a
  few hundred events over minutes, so event writes alone are unlikely to be the first limit for tens of concurrently
  active runs on one host; that was **not** measured with live agents (their cost is dominated by model latency and
  test execution, which this benchmark excludes). It is a single-file ceiling, not a statement about scale-out (mode C
  is not implemented).
- "1,000 organisations" is a data volume, not 1,000 simultaneously active tenants.
