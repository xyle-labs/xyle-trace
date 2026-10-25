"""Optional adapter startup remains actionable in a base installation."""

import builtins

from xyle_trace.mcp.__main__ import main


def test_missing_mcp_gives_public_git_install_route(monkeypatch, capsys):
    original = builtins.__import__

    def without_mcp(name, *args, **kwargs):
        if name == "xyle_trace.mcp.server":
            raise ModuleNotFoundError("No module named 'mcp'", name="mcp")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_mcp)
    assert main([]) == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert "xyle-trace[mcp] @ git+https://github.com/xyle-labs/xyle-trace.git" in output.err
