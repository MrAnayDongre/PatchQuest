# Deployment

| Mode | What it is | Status |
|---|---|---|
| **A. Local** | one process on a laptop: `patchquest run`, or `patchquest serve` + UI. SQLite in `~/.patchquest`. | implemented, tested |
| **B. Single host, queued** | API container(s) + worker container(s) sharing one SQLite volume. Runs survive a worker crash. | implemented, tested (real SIGKILL, real containers) |
| **C. Team server (PostgreSQL)** | PostgreSQL + API + workers on one or several hosts that can reach the database. | implemented; **whole test suite passes on PostgreSQL 16**; containers not yet exercised end to end (see below) |
| **D. At scale** | object storage for artifacts, SSO, per-tenant quotas, TLS | **not implemented** |

Use the words precisely: mode B is *tested* and *load-tested at the control-plane level* ([benchmarks](benchmarks.md)); nothing
here is validated for "thousands of teams" beyond those measurements.

## A. Local

```bash
pip install ./backend && patchquest doctor && patchquest run --repo . --task "..." --provider sglang --model Qwen/Qwen3-0.6B
patchquest serve                # API + (with PATCHQUEST_STATIC_DIR=frontend/dist) the UI, on 127.0.0.1
```
No token is needed on loopback. Binding elsewhere without a token refuses to start.

## B. Containers (one host)

```bash
docker compose run --rm api patchquest admin init --org Acme --workspace main --owner you   # prints a token once
docker compose up -d --scale worker=3
```
- The image (`Dockerfile`, multi-stage: Node builds the UI, a wheel carries the backend) runs as uid 10001, has a
  health check on `/ready`, and takes configuration from the environment: `PATCHQUEST_DB`, `_HOST`, `_PORT`,
  `_QUEUE_MODE`, `_WORKER_LEASE_SECONDS`, `_STATIC_DIR`, `PATCHQUEST_API_TOKEN` (optional shared token).
- `queue_mode`: the API records and queues runs; `patchquest worker` processes claim them with a lease, heartbeat,
  and recover a dead worker's run (it becomes `interrupted`, and the normal resume rules decide whether it continues:
  human edits or a half-applied patch stop it for a person).
- **Verified here:** the image was built, an org was initialised on a volume, the API and a worker ran as separate
  containers, a run went `queued -> running -> completed`, the UI was served, `/api/*` returned 401 without a token,
  and the container reported healthy. TLS is not handled: publish on loopback and put a reverse proxy in front.
- SQLite on a shared volume is safe for processes on **one host** (WAL, busy timeout, exclusive claims). Do not put the
  database on NFS or share it across hosts.
- Mount repositories read-write only where runs should be able to promote, and set `safety.allowed_roots`.

## C. Team server (PostgreSQL)

```bash
pip install 'patchquest[server]'                      # the psycopg driver is optional; the images include it
export PATCHQUEST_DATABASE_URL=postgresql://patchquest:...@db.internal:5432/patchquest
patchquest doctor                                      # shows the server version and schema, never the password
patchquest admin init --org Acme --workspace main --owner you
patchquest serve --host 0.0.0.0 &                      # API (PATCHQUEST_QUEUE_MODE=true: it queues, workers execute)
patchquest worker                                      # run on as many hosts as you like
```
`docker compose -f docker-compose.server.yml up -d --scale worker=3` does the same with a PostgreSQL container.
`PATCHQUEST_DATABASE_SCHEMA` namespaces an install inside a shared database.

- **One code path.** SQLite and PostgreSQL run the same persistence code; `dbpg.py` translates the few dialect
  differences (placeholders, `OR IGNORE`, identity columns, the append-only triggers, `lastrowid`). Local mode is
  unchanged. **Evidence:** the full backend suite (1692 tests, including real SIGKILL of API and worker processes and
  tenant-isolation tests) passes on PostgreSQL 16 in a per-test schema; the 8 skipped tests exercise SQLite files
  directly (backup/restore of a database file, legacy-file upgrade). `tests/server/` adds the multi-process
  properties: 40 queued runs claimed by 8 concurrent workers exactly once each (`FOR UPDATE SKIP LOCKED`), a dead
  worker's run recovered by exactly one of six rescuers, six processes booting together migrate once (advisory lock),
  a reader following the event cursor never skips an event while six writers commit concurrently (per-run advisory
  lock - PostgreSQL assigns ids before commit), failed transactions leave nothing, foreign keys and append-only
  triggers raise, hot queries have indexes, a lock timeout becomes `DATABASE_FAILURE`.
- **Transactions:** every `get_db()` block is one transaction on a pooled connection (default isolation, READ
  COMMITTED); state changes are compare-and-set (`UPDATE ... WHERE status = ?`), so concurrent writers cannot both win.
- **Backups:** use PostgreSQL's own tools - `pg_dump -Fc "$PATCHQUEST_DATABASE_URL" > pq.dump`, restore with
  `pg_restore -d <empty db>`. `patchquest backup` refuses on PostgreSQL and says so. Back up repositories separately.
- **Fencing:** a worker's database writes commit only while it still holds the run's lease at its epoch, so a paused
  worker that wakes up after another took over cannot add events, checkpoints or state (tested, with the fence
  disabled the test fails). A command already running when the lease moves can finish; its record is discarded.
- **Not done:** read replicas, connection-pool sizing guidance (the pool is 20 per process), and any measurement of control-plane
  throughput on PostgreSQL (the published numbers in [benchmarks](benchmarks.md) are SQLite on one host).

## Probes

`/live` - the process is up. `/ready` - the database opens, the schema version equals what this release expects,
workspace storage is writable (503 with the failing check otherwise). Both are unauthenticated and reveal nothing sensitive.

## Backups and upgrades

```bash
patchquest backup create /backups/pq-$(date +%F).db   # consistent snapshot, safe while running; written 0600 + manifest
patchquest backup verify /backups/pq-2026-10-02.db    # integrity, schema, append-only guards, every checkpoint checksum
patchquest backup restore /backups/pq-2026-10-02.db --yes   # stop API and workers first; the old file is kept
```
Migrations run on start (on PostgreSQL, under an advisory lock, so many processes may start together). Before the first
migration of an existing SQLite database a `<db>.pre-v<N>.bak` copy is written, and a database from a *newer* release is
refused. Upgrade = back up, replace the image, start; roll back = restore. On PostgreSQL take a `pg_dump` first.

## What is not here

S3-compatible artifact storage, OIDC/SSO, per-tenant quotas, a TLS terminator, Kubernetes manifests, PostgreSQL
performance numbers.
