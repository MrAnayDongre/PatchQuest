"""The exported OpenAPI document is the SDK contract: it must be valid JSON that covers the run and workflow routes."""

import json

from patchquest.cli import main


def test_openapi_export_lists_the_core_routes(capsys):
    assert main(["openapi"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["openapi"].startswith("3.")
    paths = doc["paths"]
    assert "/api/runs" in paths and "/api/workflows" in paths
    assert all(m for m in paths.values())
