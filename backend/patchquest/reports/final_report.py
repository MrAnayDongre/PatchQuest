"""Final report generation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from patchquest.orchestrator.run_context import RunContext
from patchquest.tools.secret_guard import redact_secrets


def generate_report(ctx: RunContext) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()

    sections: list[str] = []
    sections.append("# PatchQuest Run Report\n")
    sections.append(f"**Run ID:** `{ctx.run_id}`\n")
    sections.append(f"**Task:** {ctx.task}\n")
    sections.append(f"**Repository:** `{ctx.repo_path}`\n")
    sections.append(f"**Provider:** {ctx.provider}\n")
    if ctx.model:
        sections.append(f"**Model:** {ctx.model}\n")
    sections.append(f"**Runtime:** {ctx.runtime_mode}\n")
    if ctx.read_only:
        sections.append("**Mode:** Read-only (inspect/analyze)\n")
    sections.append(f"**Generated:** {now}\n")

    has_errors = len(ctx.errors) > 0
    status = "Failed" if has_errors else "Completed"
    sections.append(f"\n## Status: {status}\n")

    if has_errors:
        sections.append("\n## Errors\n")
        for err in ctx.errors:
            sections.append(f"- {err}\n")

    if ctx.analysis:
        sections.append("\n## Analysis\n\n")
        sections.append(f"{ctx.analysis.strip()}\n")

    sections.append("\n## Files Changed\n")
    if ctx.applied_files:
        for f in ctx.applied_files:
            sections.append(f"- `{f}`\n")
    else:
        sections.append("No files were modified.\n")

    sections.append("\n## Commands Executed\n")
    if ctx.commands_run:
        for cmd in ctx.commands_run:
            success = cmd.get("result", {}).get("success", False)
            icon = "pass" if success else "FAIL"
            sections.append(f"- [{icon}] `{cmd['command']}`\n")
    else:
        sections.append("No commands were executed.\n")

    sections.append("\n## Test Results\n")
    if ctx.test_results:
        for tr in ctx.test_results:
            success = tr.get("success", False)
            sections.append(f"- `{tr['command']}`: {'PASSED' if success else 'FAILED'}\n")
    else:
        sections.append("No test commands were detected or executed.\n")

    sections.append("\n## Security Scan\n")
    if ctx.secret_findings:
        sections.append(f"**{len(ctx.secret_findings)} finding(s):**\n")
        for finding in ctx.secret_findings:
            if hasattr(finding, 'finding_type'):
                sections.append(f"- {finding.finding_type}: {finding.redacted_preview}\n")
            else:
                sections.append(f"- {finding}\n")
    else:
        sections.append("No security issues detected.\n")

    if ctx.provider == "mock":
        sections.append("\n## Limitations\n")
        sections.append("- This run used the mock provider (demo mode). No actual LLM inference was performed.\n")
        sections.append("- Configure a real provider in settings for actual code changes.\n")

    report_md = redact_secrets("".join(sections))

    commands_log = "\n".join(
        f"{cmd['command']} -> {'ok' if cmd.get('result', {}).get('success') else 'fail'}"
        for cmd in ctx.commands_run
    )

    return {
        "report_md": report_md,
        "diff_patch": ctx.proposed_diff or "",
        "commands_log": commands_log,
        "events_jsonl": "",
    }


def generate_report_from_run_record(run_id: str) -> dict[str, Any]:
    """Generate a report from DB records when no pre-built report exists.

    Used for failed runs where the final_report phase never executed.
    """
    from patchquest.database import get_db

    with get_db() as conn:
        run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        events = conn.execute(
            "SELECT type, phase, status, message, payload_json FROM run_events "
            "WHERE run_id = ? ORDER BY id",
            (run_id,),
        ).fetchall()

    if not run:
        return {"report_md": f"# Run {run_id}\n\nRun not found.", "diff_patch": "", "commands_log": ""}

    now = datetime.now(timezone.utc).isoformat()
    keys = run.keys() if hasattr(run, "keys") else []
    provider = run["provider"] if "provider" in keys else "mock"
    model = run["model"] if "model" in keys else None
    runtime = run["runtime_mode"] if "runtime_mode" in keys else "local"

    sections: list[str] = []
    sections.append("# PatchQuest Run Report\n")
    sections.append(f"**Run ID:** `{run_id}`\n")
    sections.append(f"**Task:** {run['task']}\n")
    sections.append(f"**Repository:** `{run['repo_path']}`\n")
    sections.append(f"**Provider:** {provider}\n")
    if model:
        sections.append(f"**Model:** {model}\n")
    sections.append(f"**Runtime:** {runtime}\n")
    sections.append(f"**Generated:** {now}\n")

    sections.append(f"\n## Status: {run['status'].title()}\n")

    analysis_text: str | None = None
    failed_phases: list[str] = []
    completed_phases: list[str] = []
    error_messages: list[str] = []
    for evt in events:
        if evt["type"] == "analysis_generated" and evt["payload_json"]:
            try:
                payload = json.loads(evt["payload_json"])
                analysis_text = payload.get("analysis")
            except (json.JSONDecodeError, TypeError):
                pass
        if evt["status"] == "failed":
            failed_phases.append(evt["phase"] or "unknown")
            if evt["message"]:
                error_messages.append(evt["message"])
        elif evt["status"] == "complete":
            completed_phases.append(evt["phase"] or "unknown")

    if analysis_text:
        sections.append("\n## Analysis\n\n")
        sections.append(f"{analysis_text.strip()}\n")

    if completed_phases:
        sections.append("\n## Completed Phases\n")
        for p in completed_phases:
            sections.append(f"- {p}\n")

    if failed_phases:
        sections.append("\n## Failed Phases\n")
        for p in failed_phases:
            sections.append(f"- {p}\n")

    if error_messages:
        sections.append("\n## Errors\n")
        for msg in error_messages:
            sections.append(f"- {msg}\n")

    sections.append("\n## Files Changed\n")
    sections.append("No files were modified.\n")

    if provider == "mock":
        sections.append("\n## Limitations\n")
        sections.append("- This run used the mock provider (demo mode).\n")

    report_md = redact_secrets("".join(sections))
    return {"report_md": report_md, "diff_patch": "", "commands_log": ""}
