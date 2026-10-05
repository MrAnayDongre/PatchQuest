"""A small, realistic repository and tasks with known ground truth, for measuring context selection.

The repository is synthetic (no real project's code) but shaped like one: several packages with overlapping
vocabulary, two files that share a name, tests beside the code, a TypeScript front end, and documentation that
mentions everything. Every case names the files a person would need to read or edit and the symbols that matter,
so selection can be scored without a model. Add a case by appending to ``CASES`` with its ground truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

FILES: dict[str, str] = {
    "README.md": "# Shop\nPayments, billing, auth and a checkout front end. See docs for refunds, invoices, tokens and carts.\n",
    "docs/architecture.md": "Refunds go through payments.refunds. Invoices come from billing. Tokens are issued by auth. The cart lives in web/src/checkout.\n",
    "config/settings.py": "def load_settings(path):\n    return {'currency': 'USD', 'refund_limit_cents': 50000}\n",
    "payments/__init__.py": "",
    "payments/gateway.py": ("class StripeGateway:\n    def charge(self, amount_cents, token):\n        return self._post('/charges', amount_cents, token)\n\n"
                            "    def refund(self, charge_id, amount_cents):\n        return self._post('/refunds', charge_id, amount_cents)\n\n"
                            "    def _post(self, path, *args):\n        raise NotImplementedError\n"),
    "payments/refunds.py": ("from config.settings import load_settings\nfrom payments.gateway import StripeGateway\n\n\n"
                            "def process_refund(charge_id, amount_cents):\n    limit = load_settings('x')['refund_limit_cents']\n"
                            "    if amount_cents > limit:\n        return False\n    return StripeGateway().refund(charge_id, amount_cents)\n\n\n"
                            "def refund_allowed(user, amount_cents):\n    return user.get('role') == 'support'\n"),
    "payments/fees.py": ("def compute_fee(amount_cents, rate=0.029):\n    fee = int(amount_cents * rate) + 30\n    return fee\n\n\n"
                         "def net_amount(amount_cents):\n    return amount_cents - compute_fee(amount_cents)\n"),
    "payments/ledger.py": "def record_entry(account, amount_cents, memo):\n    return {'account': account, 'amount': amount_cents, 'memo': memo}\n",
    "billing/__init__.py": "",
    "billing/invoices.py": ("from billing.taxes import compute_tax\nfrom utils.money import to_cents\n\n\n"
                            "def create_invoice(customer, lines):\n    subtotal = sum(to_cents(x['price']) * x['qty'] for x in lines)\n"
                            "    return {'customer': customer, 'subtotal': subtotal, 'tax': compute_tax(subtotal)}\n\n\n"
                            "def mark_paid(invoice):\n    invoice['paid'] = True\n    return invoice\n"),
    "billing/taxes.py": "def compute_tax(subtotal_cents, rate=0.2):\n    return int(subtotal_cents * rate)\n\n\ndef tax_exempt(customer):\n    return customer.get('exempt', False)\n",
    "billing/pdf.py": "def render_invoice_pdf(invoice):\n    footer = 'Thank you for your business'\n    return f\"{invoice['customer']}|{footer}\"\n",
    "auth/__init__.py": "",
    "auth/tokens.py": ("import time\n\n\ndef issue_token(user_id, ttl_s=3600):\n    return {'sub': user_id, 'exp': time.time() + ttl_s}\n\n\n"
                       "def verify_token(token):\n    return token.get('sub') is not None\n"),
    "auth/passwords.py": "def hash_password(raw):\n    return raw[::-1]\n\n\ndef check_password(raw, hashed):\n    return hash_password(raw) == hashed\n",
    "auth/sessions.py": "def start_session(user_id):\n    return {'user': user_id}\n\n\ndef end_session(session):\n    session.clear()\n",
    "auth/permissions.py": "def can_view_invoice(user, invoice):\n    return invoice['customer'] == user['id']\n\n\ndef is_admin(user):\n    return user.get('role') == 'admin'\n",
    "users/__init__.py": "",
    "users/models.py": "class User:\n    def __init__(self, email, active=True):\n        self.email = email\n        self.active = active\n",
    "users/service.py": ("from users.emails import send_welcome\nfrom users.models import User\n\n\ndef create_user(email):\n    user = User(email)\n    send_welcome(user)\n    return user\n\n\n"
                         "def deactivate_user(user):\n    user.active = False\n    return user\n"),
    "users/emails.py": "def send_welcome(user):\n    return f'welcome {user.email}'\n\n\ndef send_goodbye(user):\n    return f'bye {user.email}'\n",
    "notifications/__init__.py": "",
    "notifications/email.py": "def send_email(to, subject, body):\n    return True\n",
    "notifications/slack.py": "def post_alert(channel, text):\n    return {'channel': channel, 'text': text}\n",
    "utils/__init__.py": "",
    "utils/dates.py": "def parse_date(text):\n    return text.split('-')\n\n\ndef business_days(start, end):\n    return 0\n",
    "utils/money.py": ("def to_cents(price):\n    return int(round(float(price) * 100))\n\n\ndef format_money(cents, currency='USD'):\n"
                       "    return f'{cents / 100:.1f} {currency}'\n"),
    "utils/text.py": "def slugify(text):\n    return text.lower().replace(' ', '-')\n",
    "utils/retry.py": ("import time\n\n\ndef with_retries(fn, attempts=3, delay_s=0.1):\n    for i in range(attempts):\n        try:\n            return fn()\n"
                       "        except Exception:\n            time.sleep(delay_s)\n    raise RuntimeError('gave up')\n"),
    "api/__init__.py": "",
    "api/routes.py": ("from auth.permissions import is_admin\nfrom billing.invoices import create_invoice\n\n\n"
                      "def get_invoice(request):\n    return create_invoice(request['user'], request['lines'])\n\n\ndef delete_user(request):\n    return is_admin(request['user'])\n"),
    "api/schemas.py": "class InvoiceSchema:\n    fields = ('customer', 'subtotal', 'tax')\n",
    "api/errors.py": "class ApiError(Exception):\n    status = 400\n",
    "web/package.json": '{"name": "shop-web", "scripts": {"test": "vitest run"}}\n',
    "web/src/checkout/cart.ts": ("export function addItem(cart: Item[], item: Item) {\n  cart.push(item)\n}\n\n"
                                 "export function total(cart: Item[]) {\n  return cart.reduce((sum, i) => sum + i.price, 0)\n}\n"),
    "web/src/checkout/cart.test.ts": "import { total } from './cart'\ntest('total', () => { expect(total([])).toBe(0) })\n",
    "web/src/checkout/payment.ts": "export async function submitPayment(cart: Item[]) {\n  return fetch('/pay', { method: 'POST' })\n}\n",
    "web/src/lib/format.ts": "export function formatPrice(cents: number) {\n  return (cents / 100).toFixed(2)\n}\n",
    "tests/payments/test_refunds.py": "from payments.refunds import process_refund\n\n\ndef test_over_limit():\n    assert process_refund('c', 10**9) is False\n",
    "tests/payments/test_fees.py": "from payments.fees import compute_fee\n\n\ndef test_fee():\n    assert compute_fee(1000) == 59\n",
    "tests/auth/test_tokens.py": "from auth.tokens import verify_token\n\n\ndef test_missing_sub():\n    assert not verify_token({})\n",
    "tests/billing/test_taxes.py": "from billing.taxes import compute_tax\n\n\ndef test_tax():\n    assert compute_tax(1000) == 200\n",
}


@dataclass(frozen=True)
class Case:
    id: str
    task: str
    relevant_files: frozenset[str]
    relevant_symbols: frozenset[tuple[str, str]]  # (file, name)
    failure_text: str = ""
    planned_files: tuple[str, ...] = ()
    notes: str = ""


def _c(id_: str, task: str, files: set[str], symbols: set[tuple[str, str]], **kw: object) -> Case:
    return Case(id_, task, frozenset(files), frozenset(symbols), **kw)  # type: ignore[arg-type]


CASES: tuple[Case, ...] = (
    _c("refund-limit", "Refunds over the configured limit are not being rejected in process_refund", {"payments/refunds.py", "tests/payments/test_refunds.py", "config/settings.py"},
       {("payments/refunds.py", "process_refund"), ("config/settings.py", "load_settings")}),
    _c("tax-rounding", "The invoice tax is rounded down: fix compute_tax so it rounds to the nearest cent", {"billing/taxes.py", "tests/billing/test_taxes.py", "billing/invoices.py"},
       {("billing/taxes.py", "compute_tax")}),
    _c("expired-token", "verify_token accepts expired tokens; it must check the exp claim", {"auth/tokens.py", "tests/auth/test_tokens.py"}, {("auth/tokens.py", "verify_token")}),
    _c("slug-dashes", "slugify leaves trailing dashes and double dashes in the result", {"utils/text.py"}, {("utils/text.py", "slugify")}),
    _c("cart-quantity", "The cart total ignores the quantity of each item on the checkout page", {"web/src/checkout/cart.ts", "web/src/checkout/cart.test.ts"},
       {("web/src/checkout/cart.ts", "total")}),
    _c("deactivated-welcome", "Deactivated users still receive welcome emails", {"users/service.py", "users/emails.py", "users/models.py"},
       {("users/service.py", "deactivate_user"), ("users/emails.py", "send_welcome")}),
    _c("charge-retry", "Gateway charges fail on a timeout; wrap them with the retry helper", {"payments/gateway.py", "utils/retry.py"},
       {("payments/gateway.py", "charge"), ("utils/retry.py", "with_retries")}),
    _c("traceback", "The nightly job crashed, please fix it", {"payments/fees.py", "tests/payments/test_fees.py"}, {("payments/fees.py", "compute_fee")},
       failure_text='Traceback (most recent call last):\n  File "payments/fees.py", line 2, in compute_fee\n    fee = int(amount_cents * rate) + 30\nTypeError: unsupported operand type(s)'),
    _c("named-file", "In billing/pdf.py change the invoice footer wording", {"billing/pdf.py"}, {("billing/pdf.py", "render_invoice_pdf")}),
    _c("decimals", "Amounts are displayed with one decimal instead of two", {"utils/money.py"}, {("utils/money.py", "format_money")},
       notes="the task never names the file or function; only the symbol vocabulary can find it"),
    _c("refund-alert", "Alert the team on Slack when a refund fails", {"notifications/slack.py", "payments/refunds.py"},
       {("notifications/slack.py", "post_alert"), ("payments/refunds.py", "process_refund")}),
    _c("invoice-permission", "The invoice route is missing a permission check", {"api/routes.py", "auth/permissions.py"},
       {("api/routes.py", "get_invoice"), ("auth/permissions.py", "can_view_invoice")}),
)


def build(root: Path) -> str:
    """Write the fixture repository under ``root`` and return its path."""
    for rel, text in FILES.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return str(root)


__all__ = ["CASES", "FILES", "Case", "build"]
