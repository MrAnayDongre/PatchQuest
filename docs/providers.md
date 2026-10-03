# Model providers and local engines

A provider turns messages into text and **declares what it can do** (`Capabilities`: JSON-object and JSON-schema
output, context window, usage reporting ...). Anything a run asks for that a provider cannot do is *degraded
explicitly* and recorded on the model call; a feature an endpoint rejects is remembered for that endpoint+model.

## Choosing one

`patchquest run --provider NAME --model M [--base-url URL]`, or per workflow node. `patchquest providers` lists the
catalogue and whether each key is configured; `patchquest engines` probes local engines (running, model loaded,
context limit, capabilities, latency, last error).

| Provider | Key variable | Notes |
|---|---|---|
| `sglang`, `vllm`, `llamacpp`, `lmstudio`, `ollama` | none | local OpenAI-compatible engines (default ports in the catalogue). SGLang and vLLM are asked to disable "thinking" for structured roles; `<think>` blocks are stripped from replies. |
| `openai_compatible` | optional | any other OpenAI-compatible URL |
| `openai`, `anthropic`, `groq`, `openrouter` | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GROQ_API_KEY`, `OPENROUTER_API_KEY` | |
| `nvidia` | `NVIDIA_API_KEY` | Responses API (`/responses`) |
| `mock`, `scripted`, `recorded` | none | deterministic fixtures / replay of a run's stored answers (never a silent fallback for a real provider) |

Keys are read from the environment at call time and redacted from errors, records and reports.

## What PatchQuest does for small models

Structured output is requested with JSON-schema constrained decoding where the engine supports it, with an adaptive
fallback if an endpoint misbehaves (e.g. loops on whitespace until the token cap), salvage of truncated JSON, and one
bounded format-repair call. Prompts are shrunk to the model's context window (probed from `/models`; blocks of file
content are trimmed proportionally and an overflow error triggers a bounded shrink-and-retry). Edits that do not apply
are fed back with the file's real contents; near-miss indentation is tolerated when the match is unambiguous.

## Retries, failover and health

Transient errors are retried centrally with jittered backoff and `Retry-After` ([failure-recovery.md](failure-recovery.md)).
Optionally configure a failover chain (`agent.failover`): it switches only for provider outages (unavailable, timeout,
rate limit) and refuses to move a local run to a cloud model (`allow_cloud`) or to lose constrained output
(`allow_capability_downgrade`) unless you allow it; refusals are events. Endpoint health (healthy / degraded / down, last
error, latency) is tracked in memory and shown by `patchquest engines` and `/api/providers/health`.
