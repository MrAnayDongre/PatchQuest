"""A fake OpenAI-compatible model endpoint (httpx.MockTransport)."""

from __future__ import annotations

import json

import httpx

OK_BODY = {"choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}], "model": "m",
           "usage": {"prompt_tokens": 11, "completion_tokens": 7}}


def completion(content: str, finish: str = "stop") -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": finish}],
                                     "usage": {"prompt_tokens": 5, "completion_tokens": 9}})


def fake_server(monkeypatch, handler):
    """Route the provider's HTTP client to ``handler(request, calls) -> httpx.Response``.

    Returns the list of recorded calls (url, headers, parsed json body), appended before the handler runs.
    """
    from patchquest.agents import providers_openai_compatible as poc

    calls: list[dict] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append({"url": str(request.url), "headers": dict(request.headers), "body": json.loads(request.content)})
        return handler(request, calls)

    monkeypatch.setattr(poc, "client_factory",
                        lambda timeout: httpx.AsyncClient(transport=httpx.MockTransport(wrapped), timeout=timeout))
    return calls
