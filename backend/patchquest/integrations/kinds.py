"""The integration kinds PatchQuest ships: what each needs to be configured, which secrets it uses, and how to build it.

Each kind declares a strict schema for its non-secret configuration and the names of its secrets. A secret is either
an environment-variable reference (``{"env": "NAME"}``) or an encrypted value stored by PatchQuest
(``{"value": "..."}``, needs ``PATCHQUEST_SECRET_KEY``). The kind's ``build`` receives resolved ``SecretRef`` objects, so
no secret value is ever held by a configuration object.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from patchquest.connectors import github, jira, linear, notion, slack, webhook
from patchquest.connectors.base import Action, Connector, ConnectorSpec, SecretRef
from patchquest.connectors.ssrf import SafeHttp


class ConfigError(ValueError):
    """The integration's configuration is not valid. The message is safe to show."""


@dataclass(frozen=True)
class Field:
    name: str
    type: str = "string"  # string | string_list | string_map
    required: bool = True
    pattern: str | None = None
    description: str = ""


@dataclass(frozen=True)
class Kind:
    name: str
    title: str
    spec: ConnectorSpec
    fields: tuple[Field, ...]
    secrets: tuple[str, ...]
    actions: Mapping[str, type[Action]]
    build: Callable[[str, dict[str, Any], dict[str, SecretRef], SafeHttp | None], Connector]
    optional_secrets: tuple[str, ...] = ()
    inbound: bool = True
    notes: str = ""
    health: str = ""  # what "test connection" does

    def describe(self) -> dict[str, Any]:
        return {"kind": self.name, "title": self.title, "inbound": self.inbound, "triggers": list(self.spec.triggers),
                "actions": [{"name": a.name, "side_effect": a.side_effect.value, "requires_approval": a.requires_approval} for a in self.spec.actions],
                "config": [{"name": f.name, "type": f.type, "required": f.required, "description": f.description} for f in self.fields],
                "secrets": [{"name": n, "required": True} for n in self.secrets] + [{"name": n, "required": False} for n in self.optional_secrets],
                "notes": self.notes, "test": self.health}


def validate_config(kind: Kind, config: Mapping[str, Any]) -> dict[str, Any]:
    unknown = set(config) - {f.name for f in kind.fields}
    if unknown:
        raise ConfigError(f"unknown setting(s) for {kind.name}: {', '.join(sorted(unknown))}")
    out: dict[str, Any] = {}
    for f in kind.fields:
        if f.name not in config:
            if f.required:
                raise ConfigError(f"'{f.name}' is required")
            continue
        value = config[f.name]
        if f.type == "string":
            if not isinstance(value, str) or not value or len(value) > 300 or (f.pattern and not re.fullmatch(f.pattern, value)):
                raise ConfigError(f"'{f.name}' is not valid" + (f" ({f.description})" if f.description else ""))
        elif f.type == "string_list":
            if not isinstance(value, list) or not value or len(value) > 50 or not all(
                    isinstance(v, str) and 0 < len(v) <= 128 and (not f.pattern or re.fullmatch(f.pattern, v)) for v in value):
                raise ConfigError(f"'{f.name}' must be a list of 1-50 valid entries" + (f" ({f.description})" if f.description else ""))
        elif f.type == "string_map":
            if not isinstance(value, dict) or not value or len(value) > 20 or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
                raise ConfigError(f"'{f.name}' must map names to URLs")
        out[f.name] = value
    return out


def _gh(ws: str, c: dict[str, Any], s: dict[str, SecretRef], http: SafeHttp | None) -> Connector:
    return github.GitHubConnector(c["repo"], token_ref=s["token"], webhook_secret_ref=s["webhook_secret"], workspace_id=ws, http=http)


def _slack(ws: str, c: dict[str, Any], s: dict[str, SecretRef], http: SafeHttp | None) -> Connector:
    return slack.SlackConnector(workspace_id=ws, bot_token_ref=s["bot_token"], signing_secret_ref=s["signing_secret"],
                                allowed_channels=tuple(c["channels"]), team_id=c.get("team_id"), http=http)


def _linear(ws: str, c: dict[str, Any], s: dict[str, SecretRef], http: SafeHttp | None) -> Connector:
    return linear.LinearConnector(workspace_id=ws, api_key_ref=s["api_key"], webhook_secret_ref=s["webhook_secret"], team_key=c.get("team_key"), http=http)


def _jira(ws: str, c: dict[str, Any], s: dict[str, SecretRef], http: SafeHttp | None) -> Connector:
    return jira.JiraConnector(workspace_id=ws, base_url=c["base_url"], project_key=c["project_key"], email_ref=s["email"],
                              api_token_ref=s["api_token"], webhook_secret_ref=s["webhook_secret"], http=http)


def _notion(ws: str, c: dict[str, Any], s: dict[str, SecretRef], http: SafeHttp | None) -> Connector:
    return notion.NotionConnector(workspace_id=ws, token_ref=s["token"], page_ids=tuple(c["page_ids"]), http=http)


def _webhook(ws: str, c: dict[str, Any], s: dict[str, SecretRef], http: SafeHttp | None) -> Connector:
    return webhook.WebhookConnector(workspace_id=ws, signing_secret_ref=s["signing_secret"], event_types=tuple(c["event_types"]),
                                    endpoints=c.get("endpoints") or {}, post_secret_ref=s.get("post_secret"), http=http)


KINDS: dict[str, Kind] = {k.name: k for k in (
    Kind("github", "GitHub", github.SPEC, (Field("repo", pattern=r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", description="owner/name"),),
         ("token", "webhook_secret"), {"comment": github.Comment, "add_label": github.AddLabel, "create_pull_request": github.CreatePullRequest},
         _gh, health="reads the repository with the token", notes="Point the repository's webhook at /hooks/<id>; content type application/json."),
    Kind("slack", "Slack", slack.SPEC, (Field("channels", "string_list", pattern=r"[CGD][A-Z0-9]{2,20}", description="channel ids PatchQuest may post to and listen in"),
                                        Field("team_id", required=False, pattern=r"T[A-Z0-9]{2,20}")),
         ("bot_token", "signing_secret"), {"post_message": slack.PostMessage}, _slack, health="auth.test with the bot token",
         notes="Set the app's Event Subscriptions request URL to /hooks/<id>; it answers Slack's url_verification."),
    Kind("linear", "Linear", linear.SPEC, (Field("team_key", required=False, pattern=r"[A-Z][A-Z0-9]{0,9}"),), ("api_key", "webhook_secret"),
         {"comment": linear.Comment}, _linear, health="viewer query with the API key", notes="Create a webhook in Linear pointing at /hooks/<id>."),
    Kind("jira", "Jira Cloud", jira.SPEC, (Field("base_url", pattern=r"https://[A-Za-z0-9.-]+", description="https://yourcompany.atlassian.net"),
                                          Field("project_key", pattern=r"[A-Z][A-Z0-9_]{1,20}")),
         ("email", "api_token", "webhook_secret"), {"comment": jira.Comment}, _jira, health="GET /myself with the API token",
         notes="Register an admin webhook with a secret pointing at /hooks/<id>."),
    Kind("notion", "Notion (read only)", notion.SPEC, (Field("page_ids", "string_list", pattern=r"[0-9a-fA-F-]{32,36}", description="the only pages PatchQuest may read"),),
         ("token",), {"read_page": notion.ReadPage}, _notion, inbound=False, health="reads the users/me endpoint",
         notes="Share each page with the Notion integration; text read from Notion is untrusted data."),
    Kind("webhook", "Generic webhook", webhook.SPEC, (Field("event_types", "string_list", pattern=r"[a-z0-9][a-z0-9_.-]{0,63}", description="the only event types accepted"),
                                                     Field("endpoints", "string_map", required=False, description="name -> https URL workflows may post to")),
         ("signing_secret",), {"post": webhook.Post}, _webhook, optional_secrets=("post_secret",), health="validates the configuration only",
         notes="Senders sign '<unix time>.<body>' with HMAC-SHA256 and send X-PatchQuest-* headers (see docs/integrations.md)."),
)}
