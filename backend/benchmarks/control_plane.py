"""Control-plane benchmark: how the API, ledger and queue behave with many tenants, runs and events.

What it measures (all against the real code and a real SQLite file; no mocks of the database):
  seeding         bulk-load organisations, workspaces, users, tokens, runs and events
  api             p50/p95 latency for the calls a UI makes, per tenant, through the ASGI app in-process
  queue           claim throughput and latency, with one claimer and with several concurrent claimers
  ledger          event append throughput, single writer and concurrent writers
  resources       database size, process RSS growth and CPU time

What it does NOT measure: network transport, a browser, model latency, agent execution time. API numbers
are in-process (no sockets), so they are the application's own cost, a lower bound for a deployment.
It never touches ~/.patchquest: the database lives in a temporary directory.

    python -m benchmarks.control_plane --orgs 1000 --runs 10000 --out benchmarks/history/
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import resource
import shutil
import statistics
import subprocess
import tempfile
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

SCHEMA_VERSION = 1


def hardware() -> dict[str, Any]:
    model = "unknown"
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                model = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    mem_kb = 0
    try:
        mem_kb = int(next(ln for ln in Path("/proc/meminfo").read_text().splitlines() if ln.startswith("MemTotal")).split()[1])
    except (OSError, StopIteration):
        pass
    return {"cpu": model, "cores": os.cpu_count(), "memory_gb": round(mem_kb / 1048576, 1), "platform": platform.platform(),
            "python": platform.python_version()}


def git_sha() -> str:
    try:
        git = shutil.which("git")
        return subprocess.run([git, "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5,  # noqa: S603
                              check=False).stdout.strip() if git else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def pct(values: list[float], p: float) -> float:
    ordered = sorted(values)
    return round(ordered[max(0, int(len(ordered) * p / 100 + 0.999999) - 1)], 3)


def summarize(ms: list[float]) -> dict[str, Any]:
    return {"n": len(ms), "p50_ms": pct(ms, 50), "p95_ms": pct(ms, 95), "max_ms": round(max(ms), 3), "mean_ms": round(statistics.fmean(ms), 3)}


def rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # Linux reports KiB


def seed(orgs: int, users_per_org: int, runs: int, events_per_run: int, hot_fraction: float) -> dict[str, Any]:
    from patchquest.database import get_db
    from patchquest.domain.identity import Role
    from patchquest.persistence import identity as ids

    t0 = time.perf_counter()
    workspaces: list[str] = []
    tokens: dict[str, str] = {}
    with get_db() as conn:
        for o in range(orgs):
            org = ids.create_org(conn, f"org{o}")
            ws = ids.create_workspace(conn, org, "main")
            workspaces.append(ws)
            for u in range(users_per_org):
                pid = ids.create_principal(conn, org, f"u{o}-{u}")
                ids.set_role(conn, pid, ws, Role.DEVELOPER if u else Role.OWNER)
                if u == 0:
                    tokens[ws] = ids.issue_token(conn, pid)[1]
    t_ident = time.perf_counter() - t0

    t1 = time.perf_counter()
    run_ids: list[tuple[str, str]] = []
    span = timedelta(days=3)  # recent, so that windowed queries (metrics over 7 days) really have data to aggregate
    base = datetime.now(UTC) - span
    hot_every = max(1, round(1 / hot_fraction)) if hot_fraction else 0  # one tenant owns this share of all runs
    statuses = ("completed", "completed", "completed", "failed", "completed", "cancelled")
    with get_db() as conn:
        for i in range(runs):
            ws = workspaces[0] if hot_every and i % hot_every == 0 else workspaces[i % len(workspaces)]
            run_id = f"r{i:07d}"
            created = (base + span * (i / runs)).isoformat()
            status = statuses[i % len(statuses)]
            conn.execute(
                "INSERT INTO runs (id, repo_path, task, status, provider, model, outcome, verdict, created_at, updated_at, completed_at, "
                "workspace_id, attempt) VALUES (?, '/repo', ?, ?, 'sglang', 'qwen', ?, ?, ?, ?, ?, ?, 1)",
                (run_id, f"task {i}", status, "applied" if status == "completed" else None, "passed" if status == "completed" else None,
                 created, created, created, ws))
            for e in range(events_per_run):
                conn.execute(
                    "INSERT INTO run_events (run_id, type, phase, message, created_at, event_uid, actor, attempt) "
                    "VALUES (?, ?, 'patching', 'bench', ?, ?, 'runtime', 1)",
                    (run_id, "phase_started" if e % 5 == 0 else "model_call", created, uuid.uuid4().hex))
            run_ids.append((run_id, ws))
    t_runs = time.perf_counter() - t1
    return {"workspaces": workspaces, "tokens": tokens, "runs": run_ids,
            "seed": {"identity_s": round(t_ident, 2), "runs_and_events_s": round(t_runs, 2), "events": runs * events_per_run,
                     "orgs": orgs, "runs": runs, "hot_tenant_runs": sum(1 for _, w in run_ids if w == workspaces[0])}}


async def bench_api(workspaces: list[str], tokens: dict[str, str], runs: list[tuple[str, str]], samples: int,
                    hot: bool = False) -> dict[str, Any]:
    from patchquest.main import app

    out: dict[str, list[float]] = {"list_runs": [], "get_run": [], "events_page": [], "metrics_7d": [], "approvals": [],
                                   "authenticate_only": []}
    if hot:  # every call is made by the tenant that owns the largest share of the data
        workspaces = workspaces[:1]
        runs = [r for r in runs if r[1] == workspaces[0]]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        for i in range(samples):
            ws = workspaces[(i * 7919) % len(workspaces)]
            headers = {"Authorization": f"Bearer {tokens[ws]}"}
            mine = [r for r, w in runs[(i * 31) % len(runs):(i * 31) % len(runs) + 400] if w == ws] or [runs[0][0]]
            for key, path in (("list_runs", "/api/runs?limit=50"), ("get_run", f"/api/runs/{mine[0]}"),
                              ("events_page", f"/api/runs/{mine[0]}/events"), ("metrics_7d", "/api/metrics?window=7d"),
                              ("approvals", f"/api/runs/{mine[0]}/approvals"), ("authenticate_only", "/api/providers/status")):
                t = time.perf_counter()
                r = await client.get(path, headers=headers)
                out[key].append((time.perf_counter() - t) * 1000)
                if r.status_code >= 500:
                    raise RuntimeError(f"{path} returned {r.status_code}")
    return {k: summarize(v) for k, v in out.items()}


def bench_queue(n_runs: int, claimers: int, prefix: str) -> dict[str, Any]:
    from patchquest.database import get_db
    from patchquest.runtime import queue

    with get_db() as conn:
        for i in range(n_runs):
            run_id = f"{prefix}{i:06d}"
            conn.execute("INSERT INTO runs (id, repo_path, task, status, created_at, updated_at, workspace_id) VALUES (?, '/r', 't', 'created', 'n', 'n', ?)",
                         (run_id, "ws_local"))
            queue.enqueue(conn, run_id, actor="bench")
    latencies: list[float] = []
    claimed: list[str] = []
    lock = threading.Lock()

    def claimer(name: str) -> None:
        while True:
            t = time.perf_counter()
            c = queue.claim(name, 30)
            elapsed = (time.perf_counter() - t) * 1000
            if c is None:
                return
            with lock:
                latencies.append(elapsed)
                claimed.append(c.run_id)

    t0 = time.perf_counter()
    threads = [threading.Thread(target=claimer, args=(f"w{i}",)) for i in range(claimers)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    wall = time.perf_counter() - t0
    return {"claimers": claimers, "runs": n_runs, "claims_per_s": round(len(claimed) / wall, 1), "duplicates": len(claimed) - len(set(claimed)),
            "claim_latency": summarize(latencies)}


def bench_ledger(writers: int, per_writer: int) -> dict[str, Any]:
    from patchquest.database import get_db
    from patchquest.persistence import ledger

    with get_db() as conn:
        conn.execute("INSERT OR IGNORE INTO runs (id, repo_path, task, created_at, updated_at) VALUES ('ledger-bench', '/r', 't', 'n', 'n')")
    latencies: list[float] = []
    lock = threading.Lock()

    def writer(n: int) -> None:
        local: list[float] = []
        for i in range(per_writer):
            t = time.perf_counter()
            with get_db() as conn:
                ledger.append(conn, "ledger-bench", "bench_event", message=f"{n}-{i}", payload={"i": i})
            local.append((time.perf_counter() - t) * 1000)
        with lock:
            latencies.extend(local)

    t0 = time.perf_counter()
    threads = [threading.Thread(target=writer, args=(n,)) for n in range(writers)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    wall = time.perf_counter() - t0
    return {"writers": writers, "events": writers * per_writer, "events_per_s": round(writers * per_writer / wall, 1),
            "append_latency": summarize(latencies)}


def bench_workload(total: int, workers: int) -> dict[str, Any]:
    """Real runs (mock provider, read-only task, tiny repository) through the whole stack: create, queue, claim, execute, complete.

    ``workers`` worker loops share one event loop, so up to that many runs are active at once. Measures what the control plane
    does per run; model latency and test execution time are excluded by construction (the mock model is instant).
    """
    from patchquest.application import TaskService
    from patchquest.database import get_db
    from patchquest.runtime.worker import Worker

    repo = Path(tempfile.mkdtemp(prefix="pq-bench-repo-"))
    (repo / "a.py").write_text("def a():\n    return 1\n")
    svc = TaskService()
    create_ms: list[float] = []
    ids_: list[str] = []
    t0 = time.perf_counter()
    for _ in range(total):
        t = time.perf_counter()
        run = svc.create_run(repo_path=str(repo), task="read only: explain the repo", provider="mock")
        svc.enqueue(run["id"])
        create_ms.append((time.perf_counter() - t) * 1000)
        ids_.append(run["id"])
    enqueue_wall = time.perf_counter() - t0

    async def drive() -> dict[str, Any]:
        stop = asyncio.Event()
        pool = [Worker(TaskService(), worker_id=f"bench-w{i}", lease_s=60, poll_s=0.05) for i in range(workers)]
        tasks = [asyncio.create_task(w.run_forever(stop)) for w in pool]
        peak = 0
        started = time.perf_counter()
        while True:
            with get_db() as conn:
                counts = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) n FROM runs WHERE repo_path = ? GROUP BY status", (str(repo),))}
                running = counts.get("running", 0)
                done = sum(counts.get(s, 0) for s in ("completed", "failed", "cancelled"))
            peak = max(peak, running)
            if done >= total or time.perf_counter() - started > 900:
                break
            await asyncio.sleep(0.1)
        wall = time.perf_counter() - started
        stop.set()
        await asyncio.gather(*tasks)
        return {"wall": wall, "peak_running": peak}

    drained = asyncio.run(drive())
    with get_db() as conn:
        rows = conn.execute("SELECT status, created_at, completed_at FROM runs WHERE repo_path = ?", (str(repo),)).fetchall()
    from datetime import datetime as dt

    lat = [(dt.fromisoformat(r["completed_at"]) - dt.fromisoformat(r["created_at"])).total_seconds() * 1000 for r in rows if r["completed_at"]]
    failed = sum(1 for r in rows if r["status"] != "completed")
    return {"runs": total, "workers": workers, "peak_concurrently_running": drained["peak_running"], "errors": failed,
            "error_rate": round(failed / total, 4), "create_and_enqueue": summarize(create_ms), "enqueue_wall_s": round(enqueue_wall, 2),
            "drain_wall_s": round(drained["wall"], 2), "runs_per_s": round(total / drained["wall"], 1),
            "queued_to_completed": summarize(lat) if lat else None}


def bench_recovery(trials: int, lease_s: float = 0.4) -> dict[str, Any]:
    """A worker claims a run and dies (never heartbeats). How long after its lease expires does another worker recover and finish it?"""
    import datetime as _dt

    from patchquest.application import TaskService
    from patchquest.database import get_db
    from patchquest.runtime import queue
    from patchquest.runtime.worker import Worker

    repo = Path(tempfile.mkdtemp(prefix="pq-bench-rec-"))
    (repo / "a.py").write_text("def a():\n    return 1\n")
    svc = TaskService()
    to_recovered: list[float] = []
    to_completed: list[float] = []

    async def trial() -> None:
        run = svc.create_run(repo_path=str(repo), task="read only: explain the repo", provider="mock")
        svc.enqueue(run["id"])
        claim = queue.claim("dead-worker", lease_s)
        if claim is None or claim.run_id != run["id"]:
            raise RuntimeError("the recovery trial needs an otherwise idle queue")
        with get_db() as conn:
            expires = _dt.datetime.fromisoformat(conn.execute("SELECT lease_expires_at FROM runs WHERE id = ?", (run["id"],)).fetchone()[0])
        rescuer = Worker(svc, worker_id="rescuer", lease_s=30, poll_s=0.05)
        recovered_at = None
        while True:
            now = _dt.datetime.now(_dt.UTC)
            if now > expires:
                await rescuer.run_once()
                with get_db() as conn:
                    status = conn.execute("SELECT status FROM runs WHERE id = ?", (run["id"],)).fetchone()[0]
                if recovered_at is None and status != "running":
                    recovered_at = (_dt.datetime.now(_dt.UTC) - expires).total_seconds() * 1000
                if status == "completed":
                    to_recovered.append(recovered_at or 0.0)
                    to_completed.append((_dt.datetime.now(_dt.UTC) - expires).total_seconds() * 1000)
                    return
            await asyncio.sleep(0.02)

    for _ in range(trials):
        asyncio.run(trial())
    return {"trials": trials, "lease_s": lease_s, "lease_expiry_to_recovered": summarize(to_recovered), "lease_expiry_to_completed": summarize(to_completed)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--orgs", type=int, default=1000)
    ap.add_argument("--users-per-org", type=int, default=5)
    ap.add_argument("--runs", type=int, default=10000)
    ap.add_argument("--events-per-run", type=int, default=40)
    ap.add_argument("--api-samples", type=int, default=300)
    ap.add_argument("--queue-runs", type=int, default=1000)
    ap.add_argument("--hot-fraction", type=float, default=0.3, help="share of all runs owned by one 'hot' tenant")
    ap.add_argument("--workload-runs", type=int, default=0, help="also drain this many real mock runs (create, queue, claim, execute)")
    ap.add_argument("--workers", type=int, default=100, help="worker loops for the workload (= peak concurrent runs)")
    ap.add_argument("--recovery-trials", type=int, default=0, help="also time worker-death recovery this many times")
    ap.add_argument("--postgres", help="run against this PostgreSQL DSN (a fresh schema is created and dropped) instead of SQLite")
    ap.add_argument("--out", help="directory for the JSON result (e.g. benchmarks/history/)")
    args = ap.parse_args(argv)

    tmp = tempfile.mkdtemp(prefix="pq-bench-")
    os.environ["PATCHQUEST_DB"] = str(Path(tmp) / "bench.db")
    from patchquest import database
    from patchquest.config import AppConfig, set_config
    from patchquest.database import init_db

    set_config(AppConfig(queue_mode=True))
    schema = None
    if args.postgres:
        import psycopg

        schema = "bench_" + uuid.uuid4().hex[:8]
        with psycopg.connect(args.postgres, autocommit=True) as admin:
            admin.execute(f"CREATE SCHEMA {schema}")
        database.use_postgres(args.postgres, schema)
    init_db()
    rss0, cpu0 = rss_mb(), time.process_time()
    loaded = seed(args.orgs, args.users_per_org, args.runs, args.events_per_run, args.hot_fraction)
    result: dict[str, Any] = {
        "schema": SCHEMA_VERSION, "git_sha": git_sha(), "timestamp": datetime.now(UTC).isoformat(), "hardware": hardware(),
        "config": {k: v for k, v in vars(args).items() if k != "out"}, "seed": loaded["seed"]}
    result["api"] = asyncio.run(bench_api(loaded["workspaces"], loaded["tokens"], loaded["runs"], args.api_samples))
    result["api_hot_tenant"] = asyncio.run(bench_api(loaded["workspaces"], loaded["tokens"], loaded["runs"], args.api_samples, hot=True))
    if args.recovery_trials:
        result["recovery"] = bench_recovery(args.recovery_trials)
    if args.workload_runs:
        result["workload"] = bench_workload(args.workload_runs, args.workers)
    result["queue"] = {"single_claimer": bench_queue(args.queue_runs // 2, 1, "qa"), "concurrent_claimers": bench_queue(args.queue_runs // 2, 8, "qb")}
    result["ledger"] = {"single_writer": bench_ledger(1, 500), "concurrent_writers": bench_ledger(8, 200)}
    result["backend"] = "postgresql" if args.postgres else "sqlite"
    if args.postgres:
        with database.get_db() as conn:
            db_mb = conn.execute("SELECT SUM(pg_total_relation_size(c.oid)) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                                 "WHERE n.nspname = current_schema() AND c.relkind IN ('r', 'i')").fetchone()[0] / 1048576
        result["resources"] = {"db_mb": round(float(db_mb), 1), "rss_growth_mb": round(rss_mb() - rss0, 1), "cpu_s": round(time.process_time() - cpu0, 1)}
    else:
        db = Path(tmp) / "bench.db"
        result["resources"] = {"db_mb": round(db.stat().st_size / 1048576, 1), "wal_mb": round(Path(str(db) + "-wal").stat().st_size / 1048576, 1)
                               if Path(str(db) + "-wal").exists() else 0.0, "rss_growth_mb": round(rss_mb() - rss0, 1),
                               "cpu_s": round(time.process_time() - cpu0, 1)}
    text = json.dumps(result, indent=2)
    print(text)
    if schema:
        database.use_sqlite()
        with psycopg.connect(args.postgres, autocommit=True) as admin:
            admin.execute(f"DROP SCHEMA {schema} CASCADE")
    if args.out:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"control-plane-{result['git_sha']}-{datetime.now(UTC):%Y%m%dT%H%M%S}.json").write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
