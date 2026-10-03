"""Deterministic, socket-free simulator of webhook senders and a minimal GitHub REST API.

MOCKED_PROTOCOL: this encodes our reading of the public GitHub/Slack docs. Passing against it proves the
connector is consistent with that reading, not that it works against the live services.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import random
import re
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass(frozen=True)
class Delivery:
    headers: dict[str, str]
    body: bytes


def github_delivery(secret: bytes, event: str, payload: dict[str, Any], delivery_id: str) -> Delivery:
    body = json.dumps(payload, separators=(",", ":")).encode()
    sig = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    return Delivery({"X-GitHub-Event": event, "X-GitHub-Delivery": delivery_id, "X-Hub-Signature-256": sig,
                     "Content-Type": "application/json"}, body)


def slack_delivery(secret: bytes, payload: dict[str, Any], timestamp: int) -> Delivery:
    body = json.dumps(payload, separators=(",", ":")).encode()
    sig = "v0=" + hmac.new(secret, f"v0:{timestamp}:".encode() + body, hashlib.sha256).hexdigest()
    return Delivery({"X-Slack-Request-Timestamp": str(timestamp), "X-Slack-Signature": sig,
                     "Content-Type": "application/json"}, body)


def duplicated(delivery: Delivery, times: int = 2) -> list[Delivery]:
    return [delivery] * times


def out_of_order(deliveries: Sequence[Delivery], seed: int = 0) -> list[Delivery]:
    """A reproducible permutation that is never the original order (for two or more deliveries)."""
    items = list(deliveries)
    rng = random.Random(seed)  # noqa: S311 - deterministic test shuffling, not security
    for _ in range(32):  # bounded: identical deliveries have no other order
        rng.shuffle(items)
        if items != list(deliveries):
            break
    return items


@dataclass(frozen=True)
class Fault:
    """What the next request should suffer instead of succeeding."""

    status: int | None = None
    retry_after: str | None = None
    timeout: bool = False
    ok: bool = False  # serve this request normally (positions a later fault)
    after_effect: bool = False  # apply the request's effect, then fail (a response lost in a crash)


def rate_limited(retry_after: str = "2") -> Fault:
    return Fault(status=429, retry_after=retry_after)


EXPIRED_CREDENTIAL = Fault(status=401)
TIMEOUT = Fault(timeout=True)
PASS = Fault(ok=True)


@dataclass
class FaultScript:
    faults: deque[Fault] = field(default_factory=deque)

    def push(self, *faults: Fault) -> None:
        self.faults.extend(faults)

    def next(self) -> Fault | None:
        fault = self.faults.popleft() if self.faults else None
        return None if fault is not None and fault.ok else fault


def _fail(fault: Fault, request: httpx.Request) -> httpx.Response:
    if fault.timeout:
        raise httpx.ReadTimeout("simulated timeout", request=request)
    headers = {"Retry-After": fault.retry_after} if fault.retry_after else {}
    return httpx.Response(fault.status or 500, headers=headers, json={"message": "simulated failure"}, request=request)


class ScriptedEndpoint:
    """A generic webhook receiver: scripted faults first, then 200. Records every request."""

    def __init__(self) -> None:
        self.script = FaultScript()
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        fault = self.script.next()
        return _fail(fault, request) if fault else httpx.Response(200, request=request)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


class MockGitHub:
    """Issues, comments, pull requests and search-by-marker for one repository."""

    def __init__(self, repo: str, token: str) -> None:
        self.repo, self._token = repo, token
        self.script = FaultScript()
        self.requests: list[httpx.Request] = []
        self.issues: dict[int, dict[str, Any]] = {}
        self.comments: dict[int, list[dict[str, Any]]] = {}
        self._ids = iter(range(1000, 10**6))
        self._next_number = 1

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def add_issue(self, title: str = "bug", body: str = "") -> int:
        number = self._next_number
        self._next_number += 1
        self.issues[number] = {"number": number, "title": title, "body": body, "labels": [], "pull_request": None,
                               "html_url": f"https://github.com/{self.repo}/issues/{number}"}
        self.comments[number] = []
        return number

    def all_comment_bodies(self) -> list[str]:
        return [c["body"] for items in self.comments.values() for c in items]

    def pulls(self) -> list[dict[str, Any]]:
        return [i for i in self.issues.values() if i["pull_request"] is not None]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("authorization") != f"Bearer {self._token}":
            return httpx.Response(401, json={"message": "Bad credentials"}, request=request)
        fault = self.script.next()
        if fault and not fault.after_effect:
            return _fail(fault, request)
        response = self._route(request)
        return _fail(fault, request) if fault else response

    def _route(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        body = json.loads(request.content) if request.content else {}
        prefix = f"/repos/{self.repo}"
        if method == "GET" and path == "/search/issues":
            return self._search(request)
        if method == "GET" and path == f"/repos/{self.repo}":
            return httpx.Response(200, json={"full_name": self.repo}, request=request)
        if not path.startswith(prefix):
            return httpx.Response(404, json={"message": "Not Found"}, request=request)
        rest = path[len(prefix):]
        if method == "POST" and rest == "/pulls":
            if not body.get("title") or not body.get("head") or not body.get("base"):
                return httpx.Response(422, json={"message": "Validation Failed"}, request=request)
            number = self.add_issue(body["title"], body.get("body", ""))
            self.issues[number]["pull_request"] = {"url": f"https://api.github.com{prefix}/pulls/{number}"}
            self.issues[number]["html_url"] = f"https://github.com/{self.repo}/pull/{number}"
            return httpx.Response(201, json=self.issues[number], request=request)
        match = re.fullmatch(r"/issues/(\d+)/(comments|labels)", rest)
        if not match or int(match.group(1)) not in self.issues:
            return httpx.Response(404, json={"message": "Not Found"}, request=request)
        number, kind = int(match.group(1)), match.group(2)
        if kind == "comments" and method == "POST":
            comment = {"id": next(self._ids), "body": body.get("body", ""),
                       "html_url": f"https://github.com/{self.repo}/issues/{number}#c{len(self.comments[number])}"}
            self.comments[number].append(comment)
            return httpx.Response(201, json=comment, request=request)
        if kind == "comments" and method == "GET":
            return httpx.Response(200, json=self.comments[number], request=request)
        if kind == "labels" and method == "POST":
            for label in body.get("labels", []):
                if label not in self.issues[number]["labels"]:  # adding an existing label is a no-op
                    self.issues[number]["labels"].append(label)
            return httpx.Response(200, json=[{"name": x} for x in self.issues[number]["labels"]], request=request)
        return httpx.Response(405, json={"message": "Method Not Allowed"}, request=request)

    def _search(self, request: httpx.Request) -> httpx.Response:
        query = request.url.params.get("q", "")
        term = re.search(r'"([^"]+)"', query)
        needle = term.group(1) if term else ""
        items = [i for n, i in self.issues.items()
                 if needle and (needle in i["body"] or any(needle in c["body"] for c in self.comments[n]))]
        return httpx.Response(200, json={"total_count": len(items), "items": items}, request=request)


# --------------------------------------------------------------------------- more senders and services
def linear_delivery(secret: bytes, payload: dict[str, Any], delivery_id: str) -> Delivery:
    body = json.dumps(payload, separators=(",", ":")).encode()
    return Delivery({"Linear-Signature": hmac.new(secret, body, hashlib.sha256).hexdigest(), "Linear-Delivery": delivery_id,
                     "Content-Type": "application/json"}, body)


def jira_delivery(secret: bytes, payload: dict[str, Any], delivery_id: str) -> Delivery:
    body = json.dumps(payload, separators=(",", ":")).encode()
    return Delivery({"X-Hub-Signature": "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest(),
                     "X-Atlassian-Webhook-Identifier": delivery_id, "Content-Type": "application/json"}, body)


def webhook_delivery(secret: bytes, event: str, payload: dict[str, Any], delivery_id: str, timestamp: int) -> Delivery:
    body = json.dumps(payload, separators=(",", ":")).encode()
    sig = "sha256=" + hmac.new(secret, f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()
    return Delivery({"X-PatchQuest-Timestamp": str(timestamp), "X-PatchQuest-Signature": sig, "X-PatchQuest-Delivery": delivery_id,
                     "X-PatchQuest-Event": event, "Content-Type": "application/json"}, body)


class _Service:
    """Shared plumbing: request log, scripted faults, a transport."""

    def __init__(self) -> None:
        self.script = FaultScript()
        self.requests: list[httpx.Request] = []

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        denied = self._authenticate(request)
        if denied is not None:
            return denied
        fault = self.script.next()
        if fault and not fault.after_effect:
            return _fail(fault, request)
        response = self._route(request)
        return _fail(fault, request) if fault else response

    def _authenticate(self, request: httpx.Request) -> httpx.Response | None:
        raise NotImplementedError

    def _route(self, request: httpx.Request) -> httpx.Response:
        raise NotImplementedError


class MockSlack(_Service):
    """chat.postMessage and conversations.history with message metadata. Errors are HTTP 200 with ok=false, as Slack does."""

    def __init__(self, token: str, channels: Sequence[str]) -> None:
        super().__init__()
        self._token = token
        self.messages: dict[str, list[dict[str, Any]]] = {c: [] for c in channels}
        self._clock = 1_760_000_000

    def _authenticate(self, request: httpx.Request) -> httpx.Response | None:
        if request.headers.get("authorization") != f"Bearer {self._token}":
            return httpx.Response(200, json={"ok": False, "error": "invalid_auth"}, request=request)
        return None

    def _route(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/chat.postMessage") and request.method == "POST":
            body = json.loads(request.content)
            channel = body.get("channel")
            if channel not in self.messages:
                return httpx.Response(200, json={"ok": False, "error": "channel_not_found"}, request=request)
            if not body.get("text"):
                return httpx.Response(200, json={"ok": False, "error": "no_text"}, request=request)
            self._clock += 1
            message = {"ts": f"{self._clock}.000100", "text": body["text"], "metadata": body.get("metadata"), "thread_ts": body.get("thread_ts")}
            self.messages[channel].append(message)
            return httpx.Response(200, json={"ok": True, "channel": channel, "ts": message["ts"]}, request=request)
        if path.endswith("/auth.test"):
            return httpx.Response(200, json={"ok": True, "user": "patchquest-bot", "team": "Acme"}, request=request)
        if path.endswith("/conversations.history") and request.method == "GET":
            channel = request.url.params.get("channel", "")
            if channel not in self.messages:
                return httpx.Response(200, json={"ok": False, "error": "channel_not_found"}, request=request)
            return httpx.Response(200, json={"ok": True, "messages": list(reversed(self.messages[channel]))[:50]}, request=request)
        return httpx.Response(404, json={"ok": False, "error": "unknown_method"}, request=request)

    def texts(self) -> list[str]:
        return [m["text"] for items in self.messages.values() for m in items]


class MockLinear(_Service):
    """commentCreate and a comments query filtered by ``body: {contains}``."""

    def __init__(self, api_key: str, issues: Sequence[str]) -> None:
        super().__init__()
        self._key = api_key
        self.comments: dict[str, list[dict[str, Any]]] = {i: [] for i in issues}
        self._ids = iter(range(1, 10**6))

    def _authenticate(self, request: httpx.Request) -> httpx.Response | None:
        if request.headers.get("authorization") != self._key:
            return httpx.Response(200, json={"errors": [{"message": "auth", "extensions": {"type": "authentication error"}}]}, request=request)
        return None

    def _route(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        query, variables = payload["query"], payload.get("variables", {})
        if "viewer" in query:
            return httpx.Response(200, json={"data": {"viewer": {"id": "u1", "name": "PatchQuest Bot"}}}, request=request)
        if "commentCreate" in query:
            data = variables["input"]
            if data["issueId"] not in self.comments:
                return httpx.Response(200, json={"errors": [{"message": "no issue", "extensions": {"type": "invalid input"}}]}, request=request)
            comment = {"id": f"c{next(self._ids)}", "body": data["body"], "url": f"https://linear.app/x/issue/{data['issueId']}#c"}
            self.comments[data["issueId"]].append(comment)
            return httpx.Response(200, json={"data": {"commentCreate": {"success": True, "comment": comment}}}, request=request)
        if "comments(" in query:
            needle = variables["f"]["body"]["contains"]
            nodes = [c for items in self.comments.values() for c in items if needle in c["body"]]
            return httpx.Response(200, json={"data": {"comments": {"nodes": nodes}}}, request=request)
        return httpx.Response(200, json={"errors": [{"message": "unknown", "extensions": {"type": "invalid input"}}]}, request=request)


class MockJira(_Service):
    """Comments (ADF) and a JQL search that understands ``comment ~ "..."`` for one project."""

    def __init__(self, email: str, token: str, project: str, issue_numbers: Sequence[int] = (1,)) -> None:
        super().__init__()
        import base64

        self._auth = "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()
        self.project = project
        self.comments: dict[str, list[dict[str, Any]]] = {f"{project}-{n}": [] for n in issue_numbers}
        self._ids = iter(range(10000, 10**6))

    def _authenticate(self, request: httpx.Request) -> httpx.Response | None:
        if request.headers.get("authorization") != self._auth:
            return httpx.Response(401, json={"errorMessages": ["unauthorized"]}, request=request)
        return None

    @staticmethod
    def text_of(body: dict[str, Any]) -> str:
        return " ".join(part["text"] for para in body["content"] for part in para.get("content", []) if part.get("type") == "text")

    def _route(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        match = re.fullmatch(r"/rest/api/3/issue/([A-Z0-9_]+-\d+)/comment", path)
        if match:
            key = match.group(1)
            if key not in self.comments:
                return httpx.Response(404, json={"errorMessages": ["Issue does not exist"]}, request=request)
            if request.method == "POST":
                comment = {"id": str(next(self._ids)), "body": json.loads(request.content)["body"]}
                self.comments[key].append(comment)
                return httpx.Response(201, json=comment, request=request)
            return httpx.Response(200, json={"comments": self.comments[key]}, request=request)
        if path == "/rest/api/3/myself":
            return httpx.Response(200, json={"displayName": "PatchQuest Bot"}, request=request)
        if path == "/rest/api/3/search":
            jql = request.url.params.get("jql", "")
            term = re.search(r'comment ~ "\\?"?([^"\\]+)', jql)
            needle = term.group(1) if term else ""
            issues = [{"key": k} for k, items in self.comments.items() if needle and any(needle in self.text_of(c["body"]) for c in items)]
            return httpx.Response(200, json={"issues": issues}, request=request)
        return httpx.Response(404, json={"errorMessages": ["not found"]}, request=request)

    def all_text(self) -> list[str]:
        return [self.text_of(c["body"]) for items in self.comments.values() for c in items]


class MockNotion(_Service):
    """Pages with paragraph blocks, paginated 2 blocks at a time."""

    def __init__(self, token: str) -> None:
        super().__init__()
        self._token = token
        self.pages: dict[str, dict[str, Any]] = {}

    def add_page(self, page_id: str, title: str, paragraphs: Sequence[str]) -> None:
        self.pages[page_id] = {"title": title, "paragraphs": list(paragraphs)}

    def _authenticate(self, request: httpx.Request) -> httpx.Response | None:
        if request.headers.get("authorization") != f"Bearer {self._token}" or request.headers.get("notion-version") is None:
            return httpx.Response(401, json={"object": "error", "code": "unauthorized"}, request=request)
        return None

    def _route(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/users/me":
            return httpx.Response(200, json={"name": "PatchQuest"}, request=request)
        page = re.fullmatch(r"/v1/pages/([0-9a-f]{32})", path)
        if page:
            data = self.pages.get(page.group(1))
            if data is None:
                return httpx.Response(404, json={"object": "error", "code": "object_not_found"}, request=request)
            return httpx.Response(200, json={"id": page.group(1), "url": f"https://notion.so/{page.group(1)}", "last_edited_time": "2026-10-01T09:00:00.000Z",
                                             "properties": {"Name": {"type": "title", "title": [{"plain_text": data["title"]}]}}}, request=request)
        blocks = re.fullmatch(r"/v1/blocks/([0-9a-f]{32})/children", path)
        if blocks and blocks.group(1) in self.pages:
            paragraphs = self.pages[blocks.group(1)]["paragraphs"]
            start = int(request.url.params.get("start_cursor", "0"))
            chunk = paragraphs[start:start + 2]
            more = start + 2 < len(paragraphs)
            return httpx.Response(200, json={"results": [{"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": t}]}} for t in chunk],
                                             "has_more": more, "next_cursor": str(start + 2) if more else None}, request=request)
        return httpx.Response(404, json={"object": "error", "code": "object_not_found"}, request=request)
