# Coding-agent systems: sourced comparison (technical due-diligence input)

All URLs were accessed on **2026-10-03**. Method: official docs / READMEs / GitHub API metadata, fetched through a summarising web-fetch tool, so some quotes are tool paraphrases. Where only a search-result summary or a third-party page was available, the cell or bullet says so. "not documented" means I did not find it in the sources I read. It does not mean the feature is absent.

Systems: Claude Code, OpenAI Codex, Aider, OpenHands, SWE-agent / mini-SWE-agent, Continue, plus two orchestration platforms chosen for relevance to the "durable runtime" question: **LangGraph** (agent graph with checkpointing) and **Temporal** (durable-execution engine, with an official agent integration).

Caveats up front:
- Docs move quickly. Codex docs redirect from developers.openai.com to learn.chatgpt.com (HTTP 308 seen today). Claude Code docs cite version numbers up to v2.1.285. Aider's latest GitHub release is v0.86.0 (2025-08-09), so its docs may lag the field.
- **Continue's main repo is read-only and "no longer actively maintained"** (see its section). Treat it as a historical comparator.

## 1. Summary table

Cell legend: ND = not documented in sources read.

| Dimension | Claude Code | Codex | Aider | OpenHands | SWE-agent / mini-SWE-agent | Continue | LangGraph | Temporal (+ Agents SDK integration) |
|---|---|---|---|---|---|---|---|---|
| 1 Durability / resume | Transcripts saved continuously as local JSONL; `--resume`/`--continue`; an in-flight tool at crash time is NOT re-run, marked as cut off; cloud sessions persist when laptop closes | Sessions persisted as JSONL rollout files and resumable (official page: "resume saved chats"; file-format detail from third-party blog) | Git auto-commits; `--restore-chat-history` (default off); no run-level recovery documented | SDK persists full event log + state to a dir; resume by same ID; pause/resume API | mini: linear history, trajectory files; resume ND. SWE-agent: `.traj` written per step (third-party summary) | ND (IDE extension; repo read-only) | Checkpointer per step (Postgres/SQLite); resume from last checkpoint after failure | Core feature: event history replayed on a new Worker; workflows run "seconds or months" |
| 2 Replay / fork | `/rewind` (code + conversation), `/branch`, `--fork-session`; bash changes and subagent edits not rewound | `/fork` creates new session from existing (third-party source); official page not seen | ND | SDK mentions time-travel debugging as a use case; fork/replay not documented on persistence page | SWE-agent: `.traj` "replayable" (third-party); mini: trajectory browser | ND | Documented replay and fork from past checkpoints; replay re-executes LLM/API calls | Replay of event history for recovery; model calls are Activities and not repeated on replay |
| 3 Sandbox / isolation | OS-enforced Bash sandbox (macOS/Linux/WSL2), shell only; file tools, MCP, hooks run outside it; cloud sessions in isolated VMs | read-only / workspace-write / danger-full-access; Seatbelt (mac), seccomp/bubblewrap (Linux) (search summary); cloud in ephemeral containers, agent internet off by default | ND (no sandbox option in the options page) | Docker sandbox (recommended), process (no isolation), remote; one pod per sandbox on K8s Enterprise | mini: local, Docker/Podman, Singularity, Bubblewrap, Contree. SWE-agent: SWE-ReX (Docker, Modal, Fargate...) | ND | None built in (orchestration only) | None built in; Agents SDK integration has pre-release Sandbox support |
| 4 Local / open-weight models | Docs state Anthropic "doesn't support routing Claude Code to non-Claude models through any gateway" | Yes: `--oss` with Ollama / LM Studio; custom providers | Yes: Ollama, LM Studio | Yes: any LLM via LiteLLM, incl. local Ollama/vLLM endpoint (search summary) | Yes: any model via litellm/OpenRouter; vLLM example (third-party) | Yes (docs mention local models; Ollama config via search) | Model-agnostic by design (not stated on pages read: ND) | Model-agnostic (workflow engine); model choice is app code |
| 5 Context selection | Agentic search of repo; CLAUDE.md/rules loaded; `/compact`, summarise-from-here | AGENTS.md; agentic (details ND) | Repo map of the git repo; user adds files | Agent explores via tools; details ND | mini: bash only, linear history; agentic | `@`-context providers (@File, @Git Diff, @Repository Map...), MCP; @Codebase deprecated | App-defined (state schema) | App-defined |
| 6 Memory / personalization | CLAUDE.md (user/project/org), `.claude/rules/`, auto memory (first 200 lines or 25KB loaded), skills | AGENTS.md; profiles; skills/plugins | Conventions file; chat history files | LLM profiles; SDK stores creds/settings in state | ND (config YAML) | Rules/config; ND detail | Store: long-term cross-thread memory; checkpointer: thread memory | Application-defined |
| 7 Workflow composition / triggers | Routines: schedule, HTTP API trigger, GitHub PR/release events; Slack @Claude; GitHub Actions/GitLab CI; hooks; subagents; Agent SDK | `codex exec` for CI; cloud tasks; GitHub `@codex review`, auto review; Slack, Linear | Scripting/CLI; watch mode for IDE comments; no triggers documented | Automations on schedule/webhook; resolver via GitHub Actions label; Jira/Linear `@openhands`/label | Batch runners (`mini-extra swebench`, `sweagent run-batch`); GitHub issue resolution is core use | CLI headless `-p` (search summary) | Graph composition, human-in-the-loop interrupts; triggers are your code | Workflows, schedules, signals; triggers are your code |
| 8 Integrations | GitHub, GitLab, Slack, MCP (Jira, Drive...), Chrome, IDEs | GitHub, Slack, Linear, IDEs, SDK | Git; IDEs via watch mode; many LLM APIs | GitHub, GitLab, Bitbucket, Jira, Slack, Linear, Notion | GitHub issues (SWE-agent) | VS Code, JetBrains, CLI; MCP | LangChain ecosystem; LangSmith | Language SDKs; OpenAI Agents SDK, MCP |
| 9 Observability | OpenTelemetry metrics, events, traces (traces beta); usage dashboards | OTel log export; analytics + Compliance API (Enterprise) | Opt-in anonymous analytics only | OTEL tracing in SDK (Laminar, Honeycomb, any OTLP) | Trajectories + trajectory browser | Telemetry removed in final 2.0.0 | LangSmith tracing; Agent Server | Web UI, event history; OTel integration (public preview) for agent spans |
| 10 Evaluation / benchmarks / RL | ND on pages read | ND on pages read | Public code-editing and refactoring leaderboards | SWE-bench-based evaluation harness; benchmark suite | Core purpose: SWE-bench, SWE-smith (open-weights model), mini >74% SWE-bench Verified (self-reported) | ND | LangSmith evals (not verified here: ND) | ND |
| 11 Team / tenancy / RBAC / audit / policy | Teams/Enterprise: SSO, managed settings, role-based permissions, compliance API; org policy toggles; managed permissions that local config cannot override | Enterprise: RBAC, `requirements.toml`, Compliance API, analytics, residency policy | ND | Enterprise tier: SSO/SAML, RBAC, audit logs, LLM budgeting (search summary of commercial tier) | ND | ND | LangSmith: workspace RBAC, SSO (OAuth2/OIDC), audit logs up to 400 days (Enterprise) | Namespaces; Cloud tenancy/RBAC details not read: ND |
| 12 Deployment overhead | Install CLI (curl/brew/winget) + Anthropic account or Bedrock/Vertex/Foundry; cloud sessions hosted by Anthropic; optional self-hosted environments, gateway | Install CLI (curl/npm/brew); ChatGPT sign-in or API key; cloud hosted by OpenAI | `pip install aider-install` | Local Docker, or Agent Server on K8s/VMs; Enterprise = Helm + license | pip install; Docker if sandboxed | Install extension / npm CLI | `pip install langgraph` + a Postgres checkpointer; self-hosted platform needs K8s/Docker, Postgres, Redis, Enterprise license | Run a cluster + database yourself, or Temporal Cloud; Worker processes; dev server via `temporal server start-dev` |
| 13 License | Proprietary; use under Anthropic Commercial / Consumer Terms (GitHub API reports no SPDX license) | Apache-2.0 | Apache-2.0 | MIT (core); `enterprise/` is source-available, paid after 1 month | MIT (both) | Apache-2.0 | MIT (library); platform needs commercial license | MIT (server and Python SDK) |

## 2. Per-system sections

### Claude Code (Anthropic)
- **Durability.** Sessions are "saved continuously" as JSONL under `~/.claude/projects/` and resumed via `--continue`/`--resume`. A tool still running when a process crashed "doesn't finish or run again"; Claude is told it was cut off and to check whether it took effect. Default retention is 30 days. https://code.claude.com/docs/en/sessions
- **Replay/fork.** `/rewind` restores code and/or conversation to any of the last 100 checkpoints. It does not track Bash-made file changes or most subagent edits and is "not a replacement for version control." `/branch` and `--fork-session` copy a transcript. https://code.claude.com/docs/en/checkpointing
- **Isolation.** The local sandbox is OS-enforced but covers shell commands only. File tools, MCP servers and hooks run outside it, and native Windows is unsandboxed. https://code.claude.com/docs/en/sandboxing. Cloud sessions run in isolated Anthropic-managed VMs with a credential proxy, or on a self-hosted environment where isolation is the customer's job. https://code.claude.com/docs/en/claude-code-on-the-web
- **Triggers/integrations.** Routines (research preview) run in the cloud on a schedule, an HTTP API call, or GitHub PR/release events. They belong to an individual account, are not shared with teammates, and cap hourly runs. https://code.claude.com/docs/en/routines. Also Slack, GitHub Actions/GitLab CI, and MCP (https://code.claude.com/docs/en/overview).
- **Observability/team.** OpenTelemetry metrics and events, with traces in beta (https://code.claude.com/docs/en/monitoring-usage). Teams/Enterprise add SSO, server-managed settings and RBAC; Bedrock/Vertex/Foundry routes use cloud IAM and audit logs (https://code.claude.com/docs/en/third-party-integrations).
- **Models.** Docs say Anthropic "doesn't support routing Claude Code to non-Claude models through any gateway" (https://code.claude.com/docs/en/llm-gateway). Local or open-weight use is therefore not supported per docs.
- **License.** Proprietary terms, not open source. Redistribution inside products is conditioned on an unmodified binary and no resale of usage (https://code.claude.com/docs/en/legal-and-compliance).
- **Better than a durable-runtime-centred harness:** polished interactive UX, built-in OS sandbox, cloud VM hosting and org policy controls out of the box. Its resume is conversation-level and does not re-run in-flight tools.

### OpenAI Codex (CLI / cloud)
- **Open source CLI**, Apache-2.0 (https://github.com/openai/codex). Installs via curl/npm/brew; sign-in with ChatGPT or API key.
- **Durability/fork.** Official CLI page lists "Resume saved chats" (https://learn.chatgpt.com/docs/codex/cli). That Codex writes JSONL rollouts under `$CODEX_HOME/sessions/` and has `/fork` comes from a third-party blog (https://codex.danielvaughan.com/2026/04/13/codex-cli-session-management-resume-fork-transcripts). I did not verify it on an official page.
- **Isolation.** Three sandbox modes (read-only, workspace-write, danger-full-access) plus approval policies (untrusted, on-request, never), per a search summary of https://developers.openai.com/codex/concepts/sandboxing. Platform mechanisms (Seatbelt; seccomp/bubblewrap) come from the same search summary. Cloud tasks run in ephemeral containers; setup scripts have internet, agent internet is off by default; containers are cached up to 12 hours (https://developers.openai.com/codex/cloud/environments, search summary).
- **Models.** `--oss` with Ollama or LM Studio; custom providers with base URL and auth helpers (https://learn.chatgpt.com/docs/config-file/config-advanced).
- **Integrations.** GitHub review via `@codex review` with rules in AGENTS.md, plus Slack, Linear, a GitHub Action and an SDK (https://learn.chatgpt.com/docs/third-party/github).
- **Observability/team.** OTel log export (config-advanced above). Enterprise: RBAC, `requirements.toml` managed policy for CLI/IDE/app, Compliance API, Analytics API, residency controls (https://learn.chatgpt.com/docs/enterprise/admin-setup).
- **Better than a durable-runtime harness:** a hosted cloud-task product with native GitHub/Slack/Linear entry points, and an open-source Apache-2.0 client.

### Aider
- **Apache-2.0** terminal pair-programmer, installed with `pip` (https://github.com/Aider-AI/aider). Last GitHub release v0.86.0 (2025-08-09); last push 2026-05-22 (GitHub API). Docs may be stale on 2026 models.
- **Context.** Maps the whole git repository to give the LLM context (https://aider.chat/docs/). Ask/code/architect modes (https://aider.chat/docs/usage/modes.html).
- **Durability.** Auto-commits of LLM changes default to true. `--restore-chat-history` defaults to false. Chat history is written to `.aider.chat.history.md` (https://aider.chat/docs/config/options.html). No crash recovery of a run is documented.
- **Models.** Cloud models plus Ollama and LM Studio.
- **Observability.** Opt-in anonymous analytics, which can be pointed at a custom PostHog.
- **Evaluation.** Maintains public leaderboards for code editing and refactoring (https://aider.chat/docs/).
- **Not documented in sources read:** sandboxing, team features, tenancy, triggers/connectors beyond git.
- **Better than a durable-runtime harness:** minimal footprint, and git itself is the audit and undo trail.

### OpenHands (formerly OpenDevin)
- **MIT** core; the `enterprise/` directory is source-available and needs a paid license beyond a 30-day trial (search summary of https://docs.openhands.dev/enterprise). README: https://github.com/OpenHands/OpenHands
- **Durability.** SDK persists the event log, LLM/tool settings, agent status, and "working directory and file system state". Restore by same ID and directory; there is also `pause()`/`run()`. The page does not address in-flight processes or fork/replay (https://docs.openhands.dev/sdk/guides/convo-persistence).
- **Isolation.** Docker sandbox recommended; Process sandbox has "no sandbox isolation"; Remote sandbox for managed deployments; K8s Enterprise runs one pod per sandbox (https://docs.openhands.dev/openhands/usage/sandboxes/overview, search summary).
- **Triggers/integrations.** README cites Slack, GitHub, Linear and Notion with schedule/webhook automations. Jira/Linear tickets can invoke it with `@openhands` or an `openhands` label (https://docs.openhands.dev/openhands/usage/cloud/project-management/overview). A GitHub Actions resolver acts on a label such as `fix-me`.
- **Observability/eval.** OTEL tracing to any OTLP backend, including Laminar and Honeycomb (https://docs.openhands.dev/sdk/guides/observability). A SWE-bench evaluation harness and wider benchmark suite are documented (https://docs.openhands.dev/usage/how-to/evaluation-harness).
- **Team.** SSO/SAML, RBAC, audit logs and LLM budgets sit in the commercial tier (search summary; confirm against the licence before relying on it).
- **Better than a durable-runtime harness:** the closest of the set to a full agent product, with sandbox, multi-user web UI, and issue-tracker triggers.

### SWE-agent / mini-SWE-agent
- **MIT** for both. SWE-agent README states most development effort is on mini-swe-agent, "which has superseded SWE-agent" (https://github.com/SWE-agent/SWE-agent). mini: https://github.com/SWE-agent/mini-swe-agent
- **Design.** mini is ~100 lines of core agent, bash only, linear message history, each action via `subprocess.run`.
- **Isolation.** Supports local, Docker/Podman, Singularity/Apptainer, Bubblewrap and Contree environments. SWE-agent uses SWE-ReX (Docker, Modal, Fargate per a third-party summary).
- **Trajectories.** mini ships a trajectory browser. SWE-agent's `.traj` files are described as replayable by a third-party source only (https://viblo.asia/p/swe-agent-deep-dive-build-your-own-guide-13VM9D5GVY7). No resume of a crashed run is documented.
- **Evaluation/RL.** README claims mini scores ">74% on SWE-bench verified" (self-reported) and lists SWE-smith, an open-weights model. A third-party page uses mini with vLLM and small open models for trajectories/RL.
- **Models.** Any model via litellm, OpenRouter or Portkey.
- **Not documented:** team features, RBAC, integrations beyond GitHub issues, memory.
- **Better than a durable-runtime harness:** tiny, auditable, strongest research and benchmark lineage.

### Continue
- **Status.** README: "no longer actively maintained and is read-only for all users"; a final 2.0.0 shipped for VS Code, CLI and JetBrains, removing anonymous telemetry and authentication (https://github.com/continuedev/continue, read via GitHub API). A third-party aggregator says the company was acquired in June 2026 (https://aiwiki.ai/wiki/continue); I could not verify this on an official page, so treat it as unconfirmed.
- **License.** Apache-2.0.
- **Context.** `@` context providers (File, Git Diff, Terminal, Repository Map, ...) with MCP recommended; @Codebase, @Docs, @Jira are deprecated (https://docs.continue.dev/customize/deep-dives/custom-providers).
- **Models/surface.** Agent/chat/edit/autocomplete in VS Code and JetBrains plus a `cn` CLI; local models supported (https://docs.continue.dev/). Headless `-p` mode comes from a search summary.
- **Not documented / likely stale:** durability, replay, sandbox, tenancy, triggers. Hub/team features were removed or unverified. Docs may predate the wind-down.
- **Better than a durable-runtime harness:** was an in-editor, autocomplete-plus-agent experience with local-model flexibility. Not a long-run runtime.

### LangGraph (orchestration platform 1)
- **MIT** library for "building, managing, and deploying long-running, stateful agents" (https://github.com/langchain-ai/langgraph).
- **Durability.** Checkpointers persist graph state per step; use Postgres/SQLite in production; checkpoint growth needs pruning (https://docs.langchain.com/oss/python/langgraph/durable-execution).
- **Replay/fork.** Replay re-executes nodes after the checkpoint ("LLM calls, API requests, and interrupts fire again and may return different results"). Fork edits state at a past checkpoint then continues (https://docs.langchain.com/oss/python/langgraph/use-time-travel).
- **Memory.** Stores hold long-term cross-thread memory.
- **Platform.** Self-hosting requires Kubernetes or Docker, PostgreSQL, Redis and an Enterprise licence key. RBAC at workspace level, OAuth2/OIDC SSO and audit logs retained up to 400 days (https://docs.langchain.com/langsmith/deploy-to-self-hosted-overview and related pages; search summary).
- **Not provided:** code-editing tools, a sandbox, or a coding UX. You build the agent.
- **Better than a durable-runtime harness:** first-class graph state, human-in-the-loop interrupts, state fork, and LangSmith tracing for LLM workflows.

### Temporal (orchestration platform 2)
- **MIT** server (https://github.com/temporalio/temporal). "Once started, a Workflow runs to completion, whether that takes seconds or months." Workers replay event history to recover state (https://docs.temporal.io/evaluate/why-temporal).
- **Agent integration.** OpenAI Agents SDK integration runs agent loops as Workflows and model calls as Activities. Worker restarts are survived. Python 3.10+, SDK 1.33.0+. Sandbox support is pre-release, streaming experimental, OTel public preview. `LocalShellTool` and `ComputerTool` are unsupported (https://docs.temporal.io/develop/python/integrations/openai-agents).
- **Overhead.** Self-hosting means running a cluster and a database. Temporal Cloud removes that (same why-temporal page).
- **Team.** Namespaces provide isolation. RBAC/audit specifics not read: not documented here.
- **Not provided:** an agent, code-editing tools, or context management. It is the substrate under one.
- **Better than a durable-runtime harness:** it is the durable runtime, with mature retry, scheduling and multi-language SDKs.

## 3. Where the differences are real vs. marketing

Only what the table and sources support.

**Real, source-backed differences**
1. **Crash semantics differ in kind.**
   - Claude Code resumes a conversation transcript and explicitly does not re-run a tool cut off by a crash.
   - OpenHands persists event log, settings and file-system state for restore by ID.
   - LangGraph and Temporal resume from checkpoints or event history. Temporal's guarantee is stated as running "to completion" and replaying activity results rather than repeating model calls.
   - Aider has no run recovery documented (only git commits).
   - So "resume" in coding-agent products means conversation resume. Only LangGraph and Temporal document step-level or replay-based recovery.
2. **Fork/replay maturity.**
   - Claude Code (`/rewind`, `/branch`) and LangGraph (replay/fork from checkpoints) document it.
   - Claude Code's rewind excludes Bash changes and most subagent edits. LangGraph's replay re-calls LLMs and APIs, so it is not deterministic re-execution.
   - Codex fork is third-party-sourced only.
   - OpenHands, Aider and Continue document none.
3. **Sandbox is built in only for some.**
   - Claude Code (shell only locally, VM in cloud), Codex (modes plus OS mechanisms, cloud containers), OpenHands (Docker/K8s) and mini-SWE-agent (several runtimes) have one.
   - Aider: ND. LangGraph: none. Temporal: pre-release Agents SDK sandbox.
4. **Local/open-weight models.**
   - Supported per docs: Codex, Aider, OpenHands, SWE-agent family, Continue.
   - Claude Code docs state they do not support non-Claude models through gateways.
5. **Enterprise controls** are documented for Claude Code, Codex, OpenHands (commercial tier), and LangSmith. They are ND for Aider, SWE-agent/mini and Continue. Claude Code routines are per-account, not shared.
6. **License split.**
   - Open: Codex, Aider, Continue (Apache-2.0); OpenHands core, SWE-agent family, LangGraph, Temporal (MIT).
   - Proprietary: Claude Code.
   - Commercial layers on open cores: OpenHands `enterprise/`, LangSmith self-hosting.
7. **Deployment overhead.** A client install for Claude Code, Codex, Aider, SWE-agent, Continue. Docker/K8s for OpenHands at scale. A database (and Redis for LangSmith) plus a cluster for Temporal and LangGraph platform.
8. **Benchmarks/RL are documented in the open research stack:** SWE-agent family (SWE-smith, SWE-bench), OpenHands (harness), Aider (leaderboards). They were not found on the Claude Code or Codex pages read.

**Not supported by the table (do not treat as differentiators)**
- Context selection claims ("understands your entire codebase", "repo map") are descriptions of approach. I found no comparative evidence on quality in these sources.
- "Memory" is mostly instruction files (CLAUDE.md, AGENTS.md, conventions). Only Claude Code's auto memory and LangGraph's Store describe learned or cross-session memory.
- Benchmark numbers such as mini's ">74%" are self-reported by the project README and were not independently checked.
- Third-party-sourced items (Codex fork and JSONL, SWE-agent `.traj` replay, Continue acquisition, OpenHands enterprise feature list) should be re-verified before inclusion in a final document.

**Gap analysis (what each does better than a durable-runtime-centred harness, in one line)**
- Claude Code: integrated sandbox, hosted VMs, org policy.
- Codex: hosted cloud tasks plus an open-source CLI.
- Aider: minimal footprint, git as audit.
- OpenHands: full multi-user agent product.
- SWE-agent/mini: research and benchmark tooling.
- Continue: IDE experience (historical).
- LangGraph: LLM-native state graph and fork.
- Temporal: the durability engine itself, but with no agent or coding tools of its own.

---

## 3. Where PatchQuest sits (our own assessment, from this repository's evidence)

The tables above are about other systems and rest on their public documentation as read on 2026-10-03 (several cells are marked not documented
or third-party-sourced; re-verify before quoting). This section is about PatchQuest and is limited to what [the repository itself](../README.md) tests or measures.

| Dimension | PatchQuest | Evidence |
|---|---|---|
| Durability | step-level: checksummed checkpoint per phase; a crash is resumed under explicit rules (drift, journaled promotion; an uncertain write is never repeated); worker leases with epoch-fenced writes | real SIGKILL tests; container kill test; [failure recovery](failure-recovery.md) |
| Replay / fork | state, recorded-model and live replay; fork from a checkpoint with another model or settings; lineage kept | [replay](replay.md), tests |
| Isolation | shadow workspace (validated before the real repo is touched), command policy, scrubbed environment, optional container runtime | [sandbox](sandbox.md); **not** a VM boundary; local-mode tests run agent-edited code with the user's privileges |
| Local / open models | first-class (Ollama, vLLM, SGLang, llama.cpp, LM Studio, OpenAI-compatible) | provider tests; live quality **weak** (Qwen3-0.6B 0/14) |
| Context | deterministic, evidence-ranked, with a measured quality harness | [evaluation](evaluation.md): 89% file recall on a synthetic fixture |
| Memory | scoped, provenance-aware, advisory; untrusted sources cannot create preferences or procedures | [memory](memory.md), poisoning tests |
| Workflows / triggers | durable workflow engine, visual builder, signed webhook ingress, human approval gates | [workflows](workflows.md), [integrations](integrations.md) |
| Integrations | GitHub, Slack, Linear, Jira, Notion, generic webhook | **simulator-verified only** |
| Observability | ledger-derived metrics, OTLP traces, operational metrics | [operations](operations.md) |
| Evaluation / RL | hidden-oracle corpus, attribution, matrix, paired experiments, gym, production-run trajectories | [evaluation](evaluation.md), [rl-gym](rl-gym.md) |
| Team / policy | RBAC, tenant isolation, teams, projects, scoped policy, audit | [tenancy](tenancy.md), [policy](policy.md); no SSO |
| Deployment | one binary + SQLite locally; PostgreSQL + workers for teams | [deployment](deployment.md) |
| License | MIT | `LICENSE` |

**What the comparison does and does not support.** The distinctive combination is a *coding-agent harness whose recovery, replay, policy and audit
are first-class and tested*, deployable without a cluster. The other systems are ahead where this one is not: hosted cloud execution and mature
IDE/CLI polish (Claude Code, Codex), a large user base and benchmark pedigree (Aider, OpenHands, SWE-agent), OS-level sandboxing out of the box
(Claude Code, Codex), a general durable-execution engine (Temporal) and an LLM-native state-graph ecosystem (LangGraph). PatchQuest has no hosted
offering, no live-verified integrations, no SSO, and the live model results it has measured are weak; none of those is a claim this repository can
make better by argument. "Better than X" is not claimed anywhere here.
