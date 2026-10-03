"""The demo repositories and the scripted model answers that fix them. Synthetic; no real company's code."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

TEST_PY = "python3 -m unittest discover -s tests -q"


def unit(name: str) -> str:
    """Run one test module: the payments suite has a known-red module (tax) that the unrelated runs must not trip over."""
    return f"python3 -m unittest discover -s tests -p test_{name}.py -q"

PAYMENTS: dict[str, str] = {
    "README.md": "# payments-service\n\nFees, refunds and tax for the checkout. Run the tests with `python3 -m unittest discover -s tests`.\n",
    "payments/__init__.py": "",
    "payments/fees.py": (
        '"""Processor fees."""\n\nRATE = 0.029\nFIXED_CENTS = 30\n\n\ndef compute_fee(amount_cents: int) -> int:\n'
        '    return int(amount_cents * RATE) + FIXED_CENTS\n\n\ndef net_amount(amount_cents: int) -> int:\n'
        '    """What the merchant receives after the processor fee."""\n    return amount_cents + compute_fee(amount_cents)\n'),
    "payments/refunds.py": (
        '"""Refunds."""\n\nREFUND_LIMIT_CENTS = 50_000\n\n\ndef process_refund(amount_cents: int, original_cents: int) -> dict:\n'
        '    if amount_cents > original_cents:\n        raise ValueError("cannot refund more than was charged")\n'
        '    return {"approved": True, "amount": amount_cents}\n'),
    "payments/currency.py": (
        '"""Price parsing."""\n\n\ndef to_cents(price: str) -> int:\n    return int(float(price) * 100)\n\n\n'
        'def format_cents(cents: int) -> str:\n    return f"{cents // 100}.{cents % 100:02d}"\n'),
    "payments/tax.py": '"""Sales tax."""\n\n\ndef compute_tax(subtotal_cents: int, rate: float = 0.2) -> int:\n    return int(subtotal_cents * rate)\n',
    "tests/__init__.py": "",
    "tests/test_fees.py": (
        "import os, sys, unittest\nsys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n"
        "from payments.fees import compute_fee, net_amount\n\n\nclass Fees(unittest.TestCase):\n"
        "    def test_fee(self):\n        self.assertEqual(compute_fee(1000), 59)\n\n"
        "    def test_net_amount_subtracts_the_fee(self):\n        self.assertEqual(net_amount(1000), 941)\n"),
    "tests/test_refunds.py": (
        "import os, sys, unittest\nsys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n"
        "from payments.refunds import process_refund\n\n\nclass Refunds(unittest.TestCase):\n"
        "    def test_small_refund_is_approved(self):\n        self.assertTrue(process_refund(2_000, 10_000)['approved'])\n\n"
        "    def test_refunds_over_the_limit_are_declined(self):\n"
        "        self.assertFalse(process_refund(60_000, 100_000)['approved'])\n"),
    "tests/test_currency.py": (
        "import os, sys, unittest\nsys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n"
        "from payments.currency import format_cents, to_cents\n\n\nclass Currency(unittest.TestCase):\n"
        "    def test_prices_convert_exactly(self):\n        self.assertEqual(to_cents('19.99'), 1999)\n\n"
        "    def test_format(self):\n        self.assertEqual(format_cents(1999), '19.99')\n"),
    "tests/test_tax.py": (
        "import os, sys, unittest\nsys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n"
        "from payments.tax import compute_tax\n\n\nclass Tax(unittest.TestCase):\n"
        "    def test_tax_rounds_to_the_nearest_cent(self):\n        self.assertEqual(compute_tax(1003), 201)\n"),
}

CHECKOUT: dict[str, str] = {
    "README.md": "# web-checkout\n\nThe cart used by the storefront. `npm test` runs the unit tests.\n",
    "package.json": '{\n  "name": "web-checkout",\n  "version": "1.0.0",\n  "scripts": {"test": "node --test"}\n}\n',
    "src/cart.js": ("function total(items) {\n  return items.reduce((sum, item) => sum + item.price, 0)\n}\n\n"
                    "function count(items) {\n  return items.reduce((n, item) => n + item.quantity, 0)\n}\n\nmodule.exports = { total, count }\n"),
    "src/cart.test.js": ("const test = require('node:test')\nconst assert = require('node:assert')\nconst { total, count } = require('./cart')\n\n"
                         "test('total multiplies price by quantity', () => {\n  assert.strictEqual(total([{ price: 250, quantity: 3 }, { price: 100, quantity: 1 }]), 850)\n})\n\n"
                         "test('count adds quantities', () => {\n  assert.strictEqual(count([{ price: 1, quantity: 2 }, { price: 1, quantity: 5 }]), 7)\n})\n"),
}


def write_repo(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    env = {"GIT_AUTHOR_NAME": "Demo", "GIT_AUTHOR_EMAIL": "demo@example.test", "GIT_COMMITTER_NAME": "Demo",
           "GIT_COMMITTER_EMAIL": "demo@example.test", "PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(root)}
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"], ["git", "commit", "-q", "-m", "Initial commit"]):
        subprocess.run(cmd, cwd=root, env=env, check=True, capture_output=True)  # noqa: S603
    return root


def edit(path: str, search: str, replace: str, why: str) -> dict[str, Any]:
    return {"edits": [{"path": path, "search": search, "replace": replace}], "create": [], "delete": [], "rationale": why}


def plan(files: list[str], summary: str, test_command: str = TEST_PY, scope: str = "1 file, a few lines") -> dict[str, Any]:
    return {"plan": summary, "files_to_inspect": files, "tests_likely_needed": [], "expected_patch_scope": scope, "stop_conditions": [],
            "test_commands": [test_command]}


REVIEW_OK = {"minimal_change": True, "unrelated_changes": False, "risk_notes": "", "missing_tests": [], "recommendation": "approve"}

FIX_NET = edit("payments/fees.py", "return amount_cents + compute_fee(amount_cents)", "return amount_cents - compute_fee(amount_cents)",
               "net_amount must subtract the processor fee, not add it")
FIX_REFUND = edit("payments/refunds.py", '    return {"approved": True, "amount": amount_cents}',
                  '    if amount_cents > REFUND_LIMIT_CENTS:\n        return {"approved": False, "reason": "over the refund limit"}\n    return {"approved": True, "amount": amount_cents}',
                  "decline refunds above REFUND_LIMIT_CENTS")
WRONG_REFUND = edit("payments/refunds.py", '    return {"approved": True, "amount": amount_cents}',
                    '    return {"approved": amount_cents < 1_000, "amount": amount_cents}', "first attempt: a limit that is far too low")
FIX_CART = edit("src/cart.js", "sum + item.price", "sum + item.price * item.quantity", "the total must include each item's quantity")
HALF_TAX = edit("payments/tax.py", "return int(subtotal_cents * rate)", "return int(subtotal_cents * rate) + 0", "attempted rounding (does not change the result)")

FIX_PRICE: dict[str, Any] = {"edits": [
    {"path": "payments/currency.py", "search": '"""Price parsing."""\n', "replace": '"""Price parsing."""\n\nfrom decimal import ROUND_HALF_UP, Decimal\n'},
    {"path": "payments/currency.py", "search": "    return int(float(price) * 100)",
     "replace": "    return int((Decimal(price) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))"}],
    "create": [], "delete": [], "rationale": "float(19.99) * 100 is 1998.999..., so int() truncates; use Decimal and round half up"}

ISSUE_WORKFLOW: dict[str, Any] = {
    "schema": 1, "name": "issue-to-fix",
    "description": "A labelled GitHub issue becomes a validated fix in an isolated workspace; after a person approves, PatchQuest comments on the "
                   "issue and tells the team in Slack. (GitHub and Slack are simulators in the demo.)",
    "trigger": {"type": "github.issues.labeled", "filter": {"payload.label": "agent-ready"}},
    "variables": {"repo": {"default": "{{PAYMENTS_REPO}}"}, "provider": {"default": "scripted"}, "model": {"default": "demo-issue"}},
    "nodes": [
        {"id": "investigate", "type": "agent", "config": {"task": "Resolve this issue: {{trigger.payload.title}}\n\n{{trigger.payload.body}}",
                                                          "repo": "{{vars.repo}}", "provider": "{{vars.provider}}", "model": "{{vars.model}}"}},
        {"id": "validated", "type": "condition", "config": {"if": {"left": "{{nodes.investigate.output.verdict}}", "op": "eq", "right": "passed"}}},
        {"id": "review", "type": "approval", "config": {"message": "Post the validated fix from run {{nodes.investigate.output.run_id}} to the issue and Slack?",
                                                      "timeout_s": 86400}},
        {"id": "comment", "type": "action", "config": {"action": "github.comment", "params": {
            "issue_number": "{{trigger.payload.number}}",
            "body": "PatchQuest validated a fix for this issue (run {{nodes.investigate.output.run_id}}); its tests pass in an isolated workspace."}}},
        {"id": "tell", "type": "action", "config": {"action": "slack.post_message", "params": {
            "channel": "C0DEMO001", "text": "Validated a fix for #{{trigger.payload.number}}: {{trigger.payload.title}}"}}},
        {"id": "note", "type": "action", "config": {"action": "notify.log", "params": {"message": "Not validated: nothing was posted"}}},
        {"id": "done", "type": "end", "config": {}},
    ],
    "layout": {"investigate": {"x": 0, "y": 0}, "validated": {"x": 240, "y": 0}, "review": {"x": 480, "y": 0}, "comment": {"x": 720, "y": 0},
               "note": {"x": 240, "y": 160}, "done": {"x": 480, "y": 160}, "tell": {"x": 720, "y": 160}},
    "edges": [{"from": "investigate", "to": "validated"}, {"from": "validated", "to": "review", "when": "true"},
              {"from": "validated", "to": "note", "when": "false"}, {"from": "review", "to": "comment", "when": "approved"},
              {"from": "review", "to": "done", "when": "denied"}, {"from": "review", "to": "done", "when": "timeout"},
              {"from": "comment", "to": "tell"}, {"from": "tell", "to": "done"}, {"from": "note", "to": "done"}],
}

ISSUE: dict[str, Any] = {"number": 412, "title": "Prices like 19.99 are charged as 19.98",
         "body": "Customers are charged one cent too little for some prices. Reproduce with to_cents('19.99') in payments/currency.py."}
