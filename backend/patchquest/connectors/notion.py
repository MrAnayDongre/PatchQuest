"""Notion connector: a read-only knowledge source.

MOCKED_PROTOCOL: tested against ``testing.mock_server.MockNotion``; never run against live Notion.

``read_page`` fetches one page from an allowlist of page ids chosen by the operator and returns its plain text with
its identity, URL and ``last_edited_time``. The text is **untrusted third-party content**: it is returned as data
(``untrusted: true``) and never stored as a preference, procedure or instruction. Nothing here writes to Notion,
and Notion offers no trigger in this connector.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from pydantic import Field

from patchquest.connectors.base import (
    Action,
    ActionResult,
    ActionSpec,
    Connector,
    ConnectorSpec,
    SecretRef,
    UnsupportedEvent,
)
from patchquest.connectors.clock import Clock, utc_now
from patchquest.connectors.envelope import EventEnvelope, SignatureStatus
from patchquest.connectors.ssrf import SafeHttp
from patchquest.domain.effects import SideEffect
from patchquest.domain.failures import FailureKind, PatchQuestError

NOTION_VERSION = "2022-06-28"
MAX_TEXT = 20000
MAX_PAGES_OF_BLOCKS = 3
_ID = re.compile(r"^[0-9a-f]{32}$|^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class ReadPage(Action):
    name = "read_page"
    page_id: str = Field(pattern=_ID.pattern)


SPEC = ConnectorSpec(name="notion", version="1", triggers=(),
                     actions=(ActionSpec("read_page", SideEffect.NETWORK_READ, requires_approval=False),), required_scopes=("read_content",))


def _plain(rich: Any) -> str:
    return "".join(str(part.get("plain_text", "")) for part in rich) if isinstance(rich, list) else ""


class NotionConnector(Connector):
    spec = SPEC

    def __init__(self, *, workspace_id: str, token_ref: SecretRef, page_ids: tuple[str, ...], http: SafeHttp | None = None,
                 api_base: str = "https://api.notion.com", clock: Clock = utc_now, environ: Mapping[str, str] | None = None) -> None:
        super().__init__(clock)
        self.workspace_id = workspace_id
        self.page_ids = tuple(p.replace("-", "").lower() for p in page_ids)
        self._token_ref, self._environ = token_ref, environ
        self._http, self._api = http or SafeHttp(), api_base.rstrip("/")

    def __repr__(self) -> str:
        return f"NotionConnector(pages={len(self.page_ids)}, token_ref={self._token_ref!r})"

    def verify(self, headers: Mapping[str, str], body: bytes) -> SignatureStatus:
        return SignatureStatus.UNVERIFIED  # there is no inbound channel: the ingress refuses integrations without triggers

    def normalize(self, raw: bytes, headers: Mapping[str, str]) -> EventEnvelope:
        raise UnsupportedEvent("notion has no triggers")

    def find_existing(self, idempotency_key: str) -> ActionResult | None:
        return None  # reads have nothing to reconcile

    def _get(self, path: str, params: Mapping[str, str] | None = None) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._token_ref.resolve(self._environ)}", "Notion-Version": NOTION_VERSION}
        response = self._http.request("GET", f"{self._api}{path}", headers=headers, params=params)
        try:
            data = response.json()
        except ValueError:
            raise PatchQuestError(FailureKind.CONNECTOR_UNAVAILABLE, "Notion returned a non-JSON body") from None
        if not isinstance(data, dict):
            raise PatchQuestError(FailureKind.CONNECTOR_UNAVAILABLE, "unexpected Notion response")
        return data

    def check(self, action: Action) -> None:
        if isinstance(action, ReadPage) and action.page_id.replace("-", "").lower() not in self.page_ids:
            raise PatchQuestError(FailureKind.POLICY_DENIED, "that page is not one this integration may read")

    def health(self) -> str:
        data = self._get("/v1/users/me")
        return f"authenticated as {data.get('name', 'the integration')}"

    def _execute(self, action: Action, idempotency_key: str) -> ActionResult:
        if not isinstance(action, ReadPage):
            raise ValueError(f"unsupported action {type(action).__name__}")
        self.check(action)
        page_id = action.page_id.replace("-", "").lower()
        try:
            page = self._get(f"/v1/pages/{page_id}")
            lines: list[str] = []
            cursor: str | None = None
            for _ in range(MAX_PAGES_OF_BLOCKS):
                params = {"page_size": "100", **({"start_cursor": cursor} if cursor else {})}
                blocks = self._get(f"/v1/blocks/{page_id}/children", params)
                for block in blocks.get("results", []):
                    body = block.get(block.get("type", ""), {})
                    text = _plain(body.get("rich_text")) if isinstance(body, dict) else ""
                    if text:
                        lines.append(text)
                cursor = blocks.get("next_cursor") if blocks.get("has_more") else None
                if not cursor:
                    break
            title = next((_plain(p.get("title")) for p in page.get("properties", {}).values() if p.get("type") == "title"), "")
            return ActionResult(page_id, page.get("url"), created=False, data={
                "title": title[:256], "text": "\n".join(lines)[:MAX_TEXT], "last_edited_time": page.get("last_edited_time"),
                "source": f"notion:{page_id}", "truncated": len("\n".join(lines)) > MAX_TEXT})
        except (KeyError, TypeError, AttributeError):
            raise PatchQuestError(FailureKind.CONNECTOR_UNAVAILABLE, "unexpected Notion response shape") from None
