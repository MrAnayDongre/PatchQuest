"""Slack connector: Events API in, ``chat.postMessage`` out.

MOCKED_PROTOCOL: tested against ``testing.mock_server.MockSlack`` (our reading of Slack's public docs); never run
against a live workspace.

* Triggers: ``app_mention`` and ``message.channels``. Messages written by bots (including this app) are ignored, so
  our own posts can never start a workflow.
* Actions: ``post_message`` to a channel on this integration's allowlist only. Text is secret-scrubbed and capped;
  PatchQuest never attaches files or source, and the workspace policy (``action.slack.post_message``) still applies.
* Idempotency: the post carries Slack message ``metadata`` with the key; ``find_existing`` looks for it in the
  recent history of the allowed channels. History is eventually consistent, and Slack may need the
  ``channels:history``/``groups:history`` scopes for the bot.
* Slack reports most API errors as HTTP 200 with ``{"ok": false, "error": ...}``; those are mapped to failure kinds here.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from pydantic import Field

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
from patchquest.connectors.webhooks import verify_slack_signature
from patchquest.domain.effects import SideEffect
from patchquest.domain.failures import FailureKind, PatchQuestError
from patchquest.tools.secret_guard import redact_secrets

TRIGGERS = ("app_mention", "message.channels")
METADATA_EVENT = "patchquest_action"
_AUTH_ERRORS = {"invalid_auth", "not_authed", "token_revoked", "token_expired", "account_inactive", "missing_scope"}


class PostMessage(Action):
    name = "post_message"
    channel: str = Field(pattern=r"^[CGD][A-Z0-9]{2,20}$")
    text: str = Field(min_length=1, max_length=3000)
    thread_ts: str | None = Field(default=None, pattern=r"^\d{10}\.\d{6}$")


SPEC = ConnectorSpec(name="slack", version="1", triggers=TRIGGERS,
                     actions=(ActionSpec("post_message", SideEffect.EXTERNAL_WRITE),),
                     required_scopes=("chat:write", "app_mentions:read", "channels:history"))


class SlackConnector(Connector):
    spec = SPEC

    def __init__(self, *, workspace_id: str, bot_token_ref: SecretRef, signing_secret_ref: SecretRef,
                 allowed_channels: tuple[str, ...], team_id: str | None = None, http: SafeHttp | None = None,
                 api_base: str = "https://slack.com/api", clock: Clock = utc_now, environ: Mapping[str, str] | None = None) -> None:
        super().__init__(clock)
        self.workspace_id, self.team_id = workspace_id, team_id
        self.allowed_channels = tuple(allowed_channels)
        self._token_ref, self._secret_ref, self._environ = bot_token_ref, signing_secret_ref, environ
        self._http, self._api = http or SafeHttp(), api_base.rstrip("/")

    def __repr__(self) -> str:
        return f"SlackConnector(channels={self.allowed_channels!r}, token_ref={self._token_ref!r})"

    # -- triggers -----------------------------------------------------------------------------
    def verify(self, headers: Mapping[str, str], body: bytes) -> SignatureStatus:
        return verify_slack_signature(self._secret_ref.resolve(self._environ).encode(), headers, body, now=self._clock().timestamp())

    def handshake(self, headers: Mapping[str, str], body: bytes) -> dict[str, Any] | None:
        try:
            data = json.loads(body)
        except ValueError:
            return None
        if isinstance(data, dict) and data.get("type") == "url_verification" and isinstance(data.get("challenge"), str):
            return {"challenge": data["challenge"]}
        return None

    def normalize(self, raw: bytes, headers: Mapping[str, str]) -> EventEnvelope:
        try:
            data = json.loads(raw)
        except (ValueError, RecursionError):
            raise MalformedEvent("body is not JSON") from None
        if not isinstance(data, dict) or data.get("type") != "event_callback":
            raise UnsupportedEvent(str(data.get("type")) if isinstance(data, dict) else "not an object")
        try:
            if self.team_id and data.get("team_id") != self.team_id:
                raise UnsupportedEvent("event is for a different Slack workspace")
            event = data["event"]
            if event.get("bot_id") or event.get("subtype"):
                raise UnsupportedEvent("bot and system messages never start workflows")
            etype = {"app_mention": "app_mention", "message": "message.channels"}.get(event["type"])
            if etype is None or (event["type"] == "message" and event.get("channel_type") != "channel"):
                raise UnsupportedEvent(str(event.get("type")))
            if self.allowed_channels and event["channel"] not in self.allowed_channels:
                raise UnsupportedEvent("event is from a channel this integration does not watch")
            ts = float(data.get("event_time") or event["ts"])
            from datetime import UTC, datetime

            return EventEnvelope(
                source="slack", type=etype, external_id=str(data["event_id"]), timestamp=datetime.fromtimestamp(ts, UTC),
                workspace_id=self.workspace_id, actor=str(event.get("user") or "") or None,
                payload={"channel": event["channel"], "user": event.get("user"), "text": str(event.get("text", ""))[:4000],
                         "ts": event["ts"], "thread_ts": event.get("thread_ts")},
                signature_status=SignatureStatus.VERIFIED)
        except UnsupportedEvent:
            raise
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise MalformedEvent(f"unexpected payload shape ({type(exc).__name__})") from None

    # -- actions ------------------------------------------------------------------------------
    def _call(self, method: str, path: str, *, body: Mapping[str, Any] | None = None, params: Mapping[str, str] | None = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._token_ref.resolve(self._environ)}"}
        content = None
        if body is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
            content = json.dumps(body).encode()
        response = self._http.request(method, f"{self._api}/{path}", headers=headers, content=content, params=params)
        try:
            data = response.json()
        except ValueError:
            raise PatchQuestError(FailureKind.CONNECTOR_UNAVAILABLE, "Slack returned a non-JSON body") from None
        if not isinstance(data, dict):
            raise PatchQuestError(FailureKind.CONNECTOR_UNAVAILABLE, "unexpected Slack response")
        if not data.get("ok"):
            error = str(data.get("error", "unknown_error"))
            if error in _AUTH_ERRORS:
                raise PatchQuestError(FailureKind.CONNECTOR_AUTH, f"Slack rejected the credentials ({error})")
            if error == "ratelimited":
                raise PatchQuestError(FailureKind.CONNECTOR_RATE_LIMIT, "Slack is rate limiting requests")
            if error in {"channel_not_found", "not_in_channel", "is_archived", "msg_too_long", "invalid_arguments", "no_text"}:
                raise PatchQuestError(FailureKind.TOOL_FAILURE, f"Slack refused the message ({error})")
            raise PatchQuestError(FailureKind.CONNECTOR_UNAVAILABLE, f"Slack error ({error})")
        return data

    def check(self, action: Action) -> None:
        if isinstance(action, PostMessage) and action.channel not in self.allowed_channels:
            raise PatchQuestError(FailureKind.POLICY_DENIED, f"channel {action.channel} is not on this integration's allowlist")

    def _execute(self, action: Action, idempotency_key: str) -> ActionResult:
        if not isinstance(action, PostMessage):
            raise ValueError(f"unsupported action {type(action).__name__}")
        self.check(action)
        body: dict[str, Any] = {"channel": action.channel, "text": redact_secrets(action.text), "unfurl_links": False,
                                "metadata": {"event_type": METADATA_EVENT, "event_payload": {"idempotency_key": idempotency_key}}}
        if action.thread_ts:
            body["thread_ts"] = action.thread_ts
        data = self._call("POST", "chat.postMessage", body=body)
        try:
            return ActionResult(f"{data['channel']}:{data['ts']}")
        except KeyError:
            raise PatchQuestError(FailureKind.CONNECTOR_UNAVAILABLE, "unexpected Slack response shape") from None

    def health(self) -> str:
        data = self._call("GET", "auth.test")
        return f"authenticated as {data.get('user', 'the bot')} in {data.get('team', 'the workspace')}"

    def find_existing(self, idempotency_key: str) -> ActionResult | None:
        for channel in self.allowed_channels:
            data = self._call("GET", "conversations.history", params={"channel": channel, "limit": "50", "include_all_metadata": "true"})
            for message in data.get("messages", []):
                meta = message.get("metadata") or {}
                if meta.get("event_type") == METADATA_EVENT and (meta.get("event_payload") or {}).get("idempotency_key") == idempotency_key:
                    return ActionResult(f"{channel}:{message['ts']}", created=False)
        return None
