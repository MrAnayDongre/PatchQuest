# Execution and the Docker sandbox

Commands the agent runs (tests, checks) execute **inside the shadow workspace**, never in your repository, through:

1. the **policy gate** (blocked / automatic / needs approval, decided from the parsed command; see [approvals.md](approvals.md)),
2. the **executor**: shell off unless a person approved a composite command, an allow-listed environment (no credential-shaped
   variables), its own process group (killed whole on timeout or cancel), bounded and drained output,
3. optionally a **container** (`--runtime docker`).

## Docker runtime

```bash
docker build -t patchquest-sandbox:latest docker/sandbox
patchquest run --repo . --task "..." --runtime docker
```
Each command runs in a named container with: no network, all capabilities dropped, read-only root filesystem, `noexec`
tmpfs, pids/memory/CPU limits, only the workspace mounted, no Docker socket, a non-root user. On timeout or cancel the
container is removed explicitly (killing the `docker` CLI alone would not stop it). If Docker is unavailable the runtime
status says so instead of crashing.

**Limits, stated plainly.** A container is not a VM: a kernel or runtime escape defeats it. The default `local` runtime has
no container and runs commands as your user, relying on the policy gate, the scrubbed environment and the shadow workspace.
Tests execute code the model edited; with the local runtime that code has your user's privileges and network. Use the
Docker runtime for untrusted repositories or weak models.

## The shadow workspace

A filtered copy of the repository: no `.git` (so a hostile `.git/config` is never read by agent commands), no secret files,
no symlinks or special files, ignored directories skipped, size capped. After a crash it is rebuilt from the checkpoint
(touched files' original and current bytes). Promotion to the real repository is atomic and hash-checked; a file that
changed under the run is a *conflict*, never overwritten.
