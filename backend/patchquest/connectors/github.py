"""GitHub connector over httpx (REST v3 shapes).

MOCKED_PROTOCOL: every test of this module runs against ``testing.mock_server.MockGitHub``. It has never
been run against github.com or GitHub Enterprise; request/response shapes follow the public docs and are
unverified live.

Idempotency: each write embeds ``<!-- patchquest:idempotency:<key> -->`` in the comment/PR body.
``find_existing`` searches for it, so a crash between "GitHub created it" and "we recorded it" cannot
duplicate on retry. Limits: GitHub's search index is eventually consistent, so a *just* created object may
not be findable yet; labels carry no body, but adding an existing label is a no-op on GitHub's side.

Credentials are SecretRefs resolved per request; nothing here stores, logs or formats the token.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from pydantic import Field, field_validator

from patchquest.connectors.base import (
    Action,
    ActionResult,
    ActionSpec,
    Connector,
    ConnectorSpec,
    MalformedEvent,
    SecretRef,
    UnsupportedEvent,
)
from patchquest.connectors.clock import Clock, utc_now
from patchquest.connectors.envelope import EventEnvelope, SignatureStatus
from patchquest.connectors.ssrf import SafeHttp
from patchquest.connectors.webhooks import verify_github_signature
from patchquest.domain.effects import SideEffect
from patchquest.domain.failures import FailureKind, PatchQuestError

MARKER_PREFIX = "patchquest:idempotency:"
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_BRANCH = re.compile(r"^[A-Za-z0-9_./:-]{1,200}$")
TRIGGERS = ("issues.opened", "issues.labeled", "pull_request.opened", "check_suite.completed")


def marker(key: str) -> str:
    return f"<!-- {MARKER_PREFIX}{key} -->"


def _no_marker(value: str) -> str:
    if MARKER_PREFIX in value:
        raise ValueError("body must not contain an idempotency marker")
    return value


class Comment(Action):
    name = "comment"
    issue_number: int = Field(gt=0)
    body: str = Field(min_length=1, max_length=60000)

    @field_validator("body")
    @classmethod
    def _body_has_no_marker(cls, value: str) -> str:
        return _no_marker(value)


class AddLabel(Action):
    name = "add_label"
    issue_number: int = Field(gt=0)
    label: str = Field(min_length=1, max_length=50, pattern=r"^[^\x00-\x1f]+$")


class CreatePullRequest(Action):
    name = "create_pull_request"
    title: str = Field(min_length=1, max_length=256)
    head: str = Field(pattern=_BRANCH.pattern)
    base: str = Field(pattern=_BRANCH.pattern)
    body: str = Field(default="", max_length=60000)

    @field_validator("body")
    @classmethod
    def _body_has_no_marker(cls, value: str) -> str:
        return _no_marker(value)


SPEC = ConnectorSpec(
    name="github", version="1", triggers=TRIGGERS,
    actions=(ActionSpec("comment", SideEffect.EXTERNAL_WRITE), ActionSpec("add_label", SideEffect.EXTERNAL_WRITE),
             ActionSpec("create_pull_request", SideEffect.EXTERNAL_WRITE)),
    required_scopes=("issues:write", "pull_requests:write"))


def _parse_time(value: Any, fallback: datetime) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return fallback
    return parsed if parsed.tzinfo else fallback


class GitHubConnector(Connector):
    spec = SPEC

    def __init__(self, repo: str, *, token_ref: SecretRef, webhook_secret_ref: SecretRef, workspace_id: str,
                 http: SafeHttp | None = None, api_base: str = "https://api.github.com", clock: Clock = utc_now,
                 environ: Mapping[str, str] | None = None) -> None:
        super().__init__(clock)
        if not _REPO.match(repo) or ".." in repo.split("/") or "." in repo.split("/"):
            raise ValueError("repo must look like 'owner/name'")
        self.repo, self.workspace_id = repo, workspace_id
        self._token_ref, self._secret_ref, self._environ = token_ref, webhook_secret_ref, environ
        self._http, self._api = http or SafeHttp(), api_base.rstrip("/")

    def __repr__(self) -> str:
        return f"GitHubConnector(repo={self.repo!r}, token_ref={self._token_ref!r})"

    # -- triggers -----------------------------------------------------------------------------
    def verify(self, headers: Mapping[str, str], body: bytes) -> SignatureStatus:
        return verify_github_signature(self._secret_ref.resolve(self._environ).encode(), headers, body)

    def normalize(self, raw: bytes, headers: Mapping[str, str]) -> EventEnvelope:
        lowered = {k.lower(): v for k, v in headers.items()}
        event, delivery = lowered.get("x-github-event"), lowered.get("x-github-delivery")
        if not event or not delivery:
            raise MalformedEvent("missing X-GitHub-Event or X-GitHub-Delivery")
        try:
            data = json.loads(raw)
        except (ValueError, RecursionError):
            raise MalformedEvent("body is not JSON") from None
        if not isinstance(data, dict):
            raise MalformedEvent("body is not a JSON object")
        etype = f"{event}.{data['action']}" if isinstance(data.get("action"), str) else event
        if etype not in TRIGGERS:
            raise UnsupportedEvent(etype)
        try:
            if data["repository"]["full_name"].lower() != self.repo.lower():
                raise UnsupportedEvent("event is for a different repository")
            now = self._clock()
            return EventEnvelope(
                source="github", type=etype, external_id=delivery, timestamp=self._event_time(data, now),
                workspace_id=self.workspace_id, actor=data["sender"]["login"], payload=self._project(data),
                signature_status=SignatureStatus.VERIFIED)
        except (KeyError, TypeError, AttributeError, ValueError) as exc:
            if isinstance(exc, UnsupportedEvent):
                raise
            raise MalformedEvent(f"unexpected payload shape ({type(exc).__name__})") from None

    @staticmethod
    def _event_time(data: dict[str, Any], fallback: datetime) -> datetime:
        for key in ("issue", "pull_request", "check_suite"):
            if key in data and "updated_at" in data[key]:
                return _parse_time(data[key]["updated_at"], fallback)
        return fallback

    @staticmethod
    def _project(data: dict[str, Any]) -> dict[str, Any]:
        """Keep only what automation needs; free text is capped and stays untrusted data."""
        out: dict[str, Any] = {"repository": data["repository"]["full_name"], "action": data.get("action")}
        for key in ("issue", "pull_request"):
            if key in data:
                obj = data[key]
                out.update(number=obj["number"], title=str(obj["title"])[:256], body=str(obj.get("body") or "")[:4000],
                           html_url=obj.get("html_url"), labels=[str(x["name"]) for x in obj.get("labels", [])])
        if "pull_request" in data:
            out.update(head_sha=data["pull_request"]["head"]["sha"], base_ref=data["pull_request"]["base"]["ref"])
        if "label" in data:
            out["label"] = str(data["label"]["name"])
        if "check_suite" in data:
            suite = data["check_suite"]
            out.update(conclusion=suite.get("conclusion"), head_sha=suite.get("head_sha"), head_branch=suite.get("head_branch"))
        return out

    # -- actions ------------------------------------------------------------------------------
    def _call(self, method: str, path: str, *, body: Mapping[str, Any] | None = None,
              params: Mapping[str, str] | None = None) -> Any:
        headers = {"Authorization": f"Bearer {self._token_ref.resolve(self._environ)}",
                   "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        content = None if body is None else json.dumps(body).encode()
        if content is not None:
            headers["Content-Type"] = "application/json"
        response = self._http.request(method, f"{self._api}{path}", headers=headers, content=content, params=params)
        try:
            return response.json()
        except ValueError:
            raise PatchQuestError(FailureKind.CONNECTOR_UNAVAILABLE, "GitHub returned a non-JSON body") from None

    def _execute(self, action: Action, idempotency_key: str) -> ActionResult:
        tag = marker(idempotency_key)
        try:
            if isinstance(action, Comment):
                data = self._call("POST", f"/repos/{self.repo}/issues/{action.issue_number}/comments",
                                  body={"body": f"{action.body}\n\n{tag}"})
                return ActionResult(str(data["id"]), data.get("html_url"))
            if isinstance(action, AddLabel):
                self._call("POST", f"/repos/{self.repo}/issues/{action.issue_number}/labels", body={"labels": [action.label]})
                return ActionResult(f"{action.issue_number}:{action.label}")
            if isinstance(action, CreatePullRequest):
                data = self._call("POST", f"/repos/{self.repo}/pulls", body={
                    "title": action.title, "head": action.head, "base": action.base, "body": f"{action.body}\n\n{tag}"})
                return ActionResult(str(data["number"]), data.get("html_url"))
        except (KeyError, TypeError):
            raise PatchQuestError(FailureKind.CONNECTOR_UNAVAILABLE, "unexpected GitHub response shape") from None
        raise ValueError(f"unsupported action {type(action).__name__}")

    def health(self) -> str:
        data = self._call("GET", f"/repos/{self.repo}")
        return f"can read {data.get('full_name', self.repo)}"

    def find_existing(self, idempotency_key: str) -> ActionResult | None:
        tag = marker(idempotency_key)
        found = self._call("GET", "/search/issues", params={"q": f'"{MARKER_PREFIX}{idempotency_key}" repo:{self.repo}'})
        try:
            for item in found["items"]:
                if item.get("pull_request") is not None:
                    if tag in (item.get("body") or ""):
                        return ActionResult(str(item["number"]), item.get("html_url"), created=False)
                    continue
                comments = self._call("GET", f"/repos/{self.repo}/issues/{item['number']}/comments", params={"per_page": "100"})
                for comment in comments:
                    if tag in (comment.get("body") or ""):
                        return ActionResult(str(comment["id"]), comment.get("html_url"), created=False)
        except (KeyError, TypeError, AttributeError):
            raise PatchQuestError(FailureKind.CONNECTOR_UNAVAILABLE, "unexpected GitHub search response") from None
        return None
