"""MOCKED_PROTOCOL_TEST: behaviours of the simulator itself."""

from __future__ import annotations

import httpx

from patchquest.connectors.testing.mock_server import (
    Fault,
    FaultScript,
    MockGitHub,
    ScriptedEndpoint,
    duplicated,
    github_delivery,
    out_of_order,
    rate_limited,
    slack_delivery,
)
from patchquest.connectors.webhooks import verify_github_signature, verify_slack_signature

TOKEN = "tok"


def _client(server: MockGitHub) -> httpx.Client:
    return httpx.Client(transport=server.transport(), base_url="https://api.github.com", headers={"Authorization": f"Bearer {TOKEN}"})


def test_deliveries_are_correctly_signed_and_duplicable_and_reorderable():
    g = github_delivery(b"s", "issues", {"a": 1}, "d1")
    assert verify_github_signature(b"s", g.headers, g.body).value == "VERIFIED"
    s = slack_delivery(b"s", {"a": 1}, 100)
    assert verify_slack_signature(b"s", s.headers, s.body, now=100).value == "VERIFIED"
    assert duplicated(g, 3) == [g, g, g]
    ds = [github_delivery(b"s", "e", {}, f"d{i}") for i in range(4)]
    assert out_of_order(ds, seed=1) != ds and sorted(x.headers["X-GitHub-Delivery"] for x in out_of_order(ds)) == ["d0", "d1", "d2", "d3"]
    assert out_of_order([g, g]) == [g, g]  # terminates


def test_fault_script_fifo():
    fs = FaultScript()
    fs.push(Fault(status=500), Fault(timeout=True))
    assert fs.next() == Fault(status=500) and fs.next() == Fault(timeout=True) and fs.next() is None


def test_endpoint_faults_then_ok():
    ep = ScriptedEndpoint()
    ep.script.push(rate_limited("3"), Fault(status=401))
    with httpx.Client(transport=ep.transport()) as c:
        r1, r2, r3 = (c.post("https://x.example/") for _ in range(3))
    assert (r1.status_code, r1.headers["retry-after"], r2.status_code, r3.status_code) == (429, "3", 401, 200)


def test_mock_github_rest_behaviour():
    server = MockGitHub("o/r", TOKEN)
    n = server.add_issue()
    with _client(server) as c:
        assert c.post(f"/repos/o/r/issues/{n}/comments", json={"body": "needle-1"}).status_code == 201
        assert c.post("/repos/o/r/pulls", json={"title": "t", "head": "h", "base": "b", "body": "needle-2"}).status_code == 201
        assert c.post("/repos/o/r/pulls", json={"title": "t"}).status_code == 422
        assert c.post("/repos/o/r/issues/99/comments", json={"body": "x"}).status_code == 404
        assert c.post(f"/repos/o/r/issues/{n}/labels", json={"labels": ["a", "a"]}).json() == [{"name": "a"}]
        hits = c.get("/search/issues", params={"q": '"needle-2" repo:o/r'}).json()["items"]
        assert len(hits) == 1 and hits[0]["pull_request"] is not None
        assert c.get("/search/issues", params={"q": '"nope" repo:o/r'}).json()["items"] == []
        assert c.get(f"/repos/o/r/issues/{n}/comments").json()[0]["body"] == "needle-1"
    with httpx.Client(transport=server.transport(), base_url="https://api.github.com") as anon:
        assert anon.get("/search/issues").status_code == 401


def test_mock_github_faults_including_effect_then_timeout():
    server = MockGitHub("o/r", TOKEN)
    n = server.add_issue()
    server.script.push(Fault(status=429, retry_after="5"), Fault(timeout=True, after_effect=True))
    with _client(server) as c:
        assert c.post(f"/repos/o/r/issues/{n}/comments", json={"body": "x"}).headers["retry-after"] == "5"
        try:
            c.post(f"/repos/o/r/issues/{n}/comments", json={"body": "y"})
        except httpx.ReadTimeout:
            pass
        else:
            raise AssertionError("expected a timeout")
    assert server.all_comment_bodies() == ["y"]
