# Deployment

| Mode | What it is | Status |
|---|---|---|
| **A. Local** | one process on a laptop: `patchquest run`, or `patchquest serve` + UI. SQLite in `~/.patchquest`. | implemented, tested |
| **B. Single host, queued** | API container(s) + worker container(s) sharing one SQLite volume. Runs survive a worker crash. | implemented, tested (real SIGKILL, real containers) |
| **C. Multi-host / multi-tenant at scale** | workers on several machines, a network database, object storage | **not implemented** (designed only) |

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

## Probes

`/live` - the process is up. `/ready` - the database opens, the schema version equals what this release expects,
workspace storage is writable (503 with the failing check otherwise). Both are unauthenticated and reveal nothing sensitive.

## Backups and upgrades

```bash
patchquest backup create /backups/pq-$(date +%F).db   # consistent snapshot, safe while running; written 0600 + manifest
patchquest backup verify /backups/pq-2026-10-02.db    # integrity, schema, append-only guards, every checkpoint checksum
patchquest backup restore /backups/pq-2026-10-02.db --yes   # stop API and workers first; the old file is kept
```
Migrations run on start. Before the first migration of an existing database a `<db>.pre-v<N>.bak` copy is written, and a
database from a *newer* release is refused. Upgrade = back up, replace the image, start; roll back = restore.

## What is not here

PostgreSQL, S3-compatible artifact storage, OIDC/SSO, per-tenant quotas, a TLS terminator, Kubernetes manifests.
Moving to mode C needs the database layer abstracted (today it is SQLite-specific SQL) and write fencing by lease epoch.
