"""Tests for SecretGuard."""

from patchquest.tools.secret_guard import has_secrets, redact_secrets, scan_diff, scan_text


def test_detects_openai_key():
    text = 'api_key = "sk-abc123def456ghi789jkl012mno345pqr678"'
    findings = scan_text(text)
    assert len(findings) > 0
    assert any("OpenAI" in f.finding_type for f in findings)


def test_detects_github_token():
    text = 'token = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmn"'
    findings = scan_text(text)
    assert len(findings) > 0
    assert any("GitHub" in f.finding_type for f in findings)


def test_detects_aws_key():
    text = "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"
    findings = scan_text(text)
    assert len(findings) > 0
    assert any("AWS" in f.finding_type for f in findings)


def test_detects_private_key():
    text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA..."
    findings = scan_text(text)
    assert len(findings) > 0
    assert any("Private Key" in f.finding_type for f in findings)


def test_redacts_secrets():
    text = 'key = "sk-abc123def456ghi789jkl012mno345pqr678"'
    redacted = redact_secrets(text)
    assert "sk-abc123" not in redacted
    assert "[REDACTED]" in redacted


def test_blocks_diff_with_secret():
    diff = """+++ b/config.py
@@ -1,3 +1,4 @@
+API_KEY = "sk-abc123def456ghi789jkl012mno345pqr678"
 import os
"""
    findings = scan_diff(diff)
    assert len(findings) > 0


def test_does_not_flag_normal_code():
    text = """
def calculate_total(items):
    total = sum(item.price for item in items)
    return total

class UserService:
    def get_user(self, user_id: int):
        return self.db.query(User).get(user_id)
"""
    findings = scan_text(text)
    assert len(findings) == 0


def test_does_not_flag_short_strings():
    text = 'name = "hello"'
    findings = scan_text(text)
    assert len(findings) == 0


def test_has_secrets_helper():
    assert has_secrets('key = "sk-abc123def456ghi789jkl012mno345pqr678"')
    assert not has_secrets("x = 42")
