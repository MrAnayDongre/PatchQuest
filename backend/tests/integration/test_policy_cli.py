"""The policy and config-explain commands, end to end through the real parser."""

import json

from patchquest.cli import main


def run(capsys, *argv):
    code = main(list(argv))
    return code, capsys.readouterr()


def test_put_list_explain_and_disable(tmp_path, capsys):
    f = tmp_path / "p.yaml"
    f.write_text("name: prod-safe\nscope: workspace\nrules:\n  - {action: 'action.github.*', result: DENY, reason: frozen for release}\n"
                 "limits: {agent.max_model_calls: 12}\n")
    code, out = run(capsys, "policy", "put", str(f), "--ref", "ws_local")
    assert code == 0 and "version 1" in out.out

    code, out = run(capsys, "policy", "explain", "action.github.comment", "--effect", "EXTERNAL_WRITE")
    assert code == 0 and "DENY" in out.out and "prod-safe" in out.out and "frozen for release" in out.out

    code, out = run(capsys, "policy", "explain", "action.log.note", "--effect", "READ_ONLY", "--json")
    assert json.loads(out.out)["result"] == "ALLOW"

    code, out = run(capsys, "config", "explain", "--key", "max_model_calls")
    assert code == 0 and "from policy: prod-safe" in out.out and "overrode default" in out.out

    code, out = run(capsys, "policy", "disable", "prod-safe", "--scope", "workspace", "--ref", "ws_local")
    assert code == 0
    code, out = run(capsys, "policy", "explain", "action.github.comment", "--effect", "EXTERNAL_WRITE")
    assert "REQUIRE_APPROVAL" in out.out and "system-floor" in out.out


def test_a_bad_policy_file_is_refused_with_a_reason(tmp_path, capsys):
    f = tmp_path / "bad.yaml"
    f.write_text("name: x\nscope: user\nrules: [{action: y, result: PERHAPS}]\n")
    code, out = run(capsys, "policy", "put", str(f), "--ref", "u1")
    assert code == 1 and "result must be one of" in out.err
    code, out = run(capsys, "policy", "list")
    assert out.out.strip() == ""
