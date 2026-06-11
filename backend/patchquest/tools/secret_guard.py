"""SecretGuard - detects and redacts secrets in text content.

Scans diffs, command output, memory records, and reports for leaked secrets.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class SecretFinding:
    finding_type: str
    line_number: int | None
    file_path: str | None
    redacted_preview: str
    remediation: str


SECRET_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("OpenAI API Key", re.compile(r"sk-[a-zA-Z0-9]{20,}"), "Use os.environ['OPENAI_API_KEY'] instead."),
    ("Anthropic API Key", re.compile(r"sk-ant-[a-zA-Z0-9\-]{20,}"), "Use os.environ['ANTHROPIC_API_KEY'] instead."),
    ("GitHub Token", re.compile(r"gh[pousr]_[A-Za-z0-9_]{36,}"), "Use os.environ['GITHUB_TOKEN'] instead."),
    ("GitHub Personal Token", re.compile(r"github_pat_[A-Za-z0-9_]{22,}"), "Use os.environ['GITHUB_TOKEN'] instead."),
    ("AWS Access Key ID", re.compile(r"AKIA[0-9A-Z]{16}"), "Use os.environ['AWS_ACCESS_KEY_ID'] instead."),
    ("AWS Secret Key", re.compile(r"(?i)(aws_secret_access_key|aws_secret)\s*[=:]\s*['\"]?[A-Za-z0-9/+=]{40}"), "Use os.environ['AWS_SECRET_ACCESS_KEY'] instead."),
    ("Google API Key", re.compile(r"AIza[0-9A-Za-z\-_]{35}"), "Use os.environ['GOOGLE_API_KEY'] instead."),
    ("Slack Token", re.compile(r"xox[baprs]-[0-9]{10,13}-[0-9]{10,13}-[a-zA-Z0-9]{24,}"), "Store Slack tokens in environment variables."),
    ("Private Key Block", re.compile(r"-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----"), "Never commit private keys. Use a secret manager."),
    ("JWT Token", re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "Do not hardcode JWT tokens."),
    ("Generic Bearer Token", re.compile(r"(?i)bearer\s+[a-zA-Z0-9\-._~+/]{20,}"), "Store bearer tokens in environment variables."),
    ("Connection String Password", re.compile(r"(?i)(mongodb|postgres|mysql|redis)://[^:]+:[^@\s]{8,}@"), "Use environment variables for connection strings."),
]

SUSPICIOUS_ASSIGNMENT_PATTERN = re.compile(
    r"""(?i)(?:api_key|apikey|secret|token|password|passwd|pwd|credential|private_key|access_key|auth_token|bearer)\s*[=:]\s*['"][^'"]{8,}['"]""",
)

HIGH_ENTROPY_NEAR_SUSPECT = re.compile(
    r"""(?i)(?:key|secret|token|password|api)\s*[=:]\s*['"]([A-Za-z0-9+/=\-_]{20,})['"]""",
)


def scan_text(text: str, file_path: str | None = None) -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    lines = text.split("\n")

    for line_idx, line in enumerate(lines, 1):
        for finding_type, pattern, remediation in SECRET_PATTERNS:
            if pattern.search(line):
                findings.append(SecretFinding(
                    finding_type=finding_type,
                    line_number=line_idx,
                    file_path=file_path,
                    redacted_preview=_redact_line(line),
                    remediation=remediation,
                ))

        if SUSPICIOUS_ASSIGNMENT_PATTERN.search(line):
            already_found = any(f.line_number == line_idx for f in findings)
            if not already_found:
                findings.append(SecretFinding(
                    finding_type="Suspicious Secret Assignment",
                    line_number=line_idx,
                    file_path=file_path,
                    redacted_preview=_redact_line(line),
                    remediation="Use environment variables instead of hardcoded secrets.",
                ))

    return findings


def scan_diff(diff_text: str) -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    current_file: str | None = None
    line_in_file = 0

    for line in diff_text.split("\n"):
        if line.startswith("+++ b/"):
            current_file = line[6:]
            line_in_file = 0
        elif line.startswith("@@"):
            match = re.search(r"\+(\d+)", line)
            if match:
                line_in_file = int(match.group(1)) - 1
        elif line.startswith("+") and not line.startswith("+++"):
            line_in_file += 1
            added_content = line[1:]
            line_findings = scan_text(added_content, file_path=current_file)
            for f in line_findings:
                f.line_number = line_in_file
                findings.append(f)
        elif not line.startswith("-"):
            line_in_file += 1

    return findings


def redact_secrets(text: str) -> str:
    result = text
    for _, pattern, _ in SECRET_PATTERNS:
        result = pattern.sub("[REDACTED]", result)
    result = SUSPICIOUS_ASSIGNMENT_PATTERN.sub(
        lambda m: m.group(0)[:m.start(0)] + "[REDACTED]" if m.start(0) > 0 else "[REDACTED]",
        result,
    )
    return result


def _redact_line(line: str) -> str:
    redacted = line.strip()
    if len(redacted) > 80:
        redacted = redacted[:40] + "...[REDACTED]..." + redacted[-10:]
    for _, pattern, _ in SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def has_secrets(text: str) -> bool:
    return len(scan_text(text)) > 0
