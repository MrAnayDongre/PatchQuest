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
