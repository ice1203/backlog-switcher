"""shell.py（シェル統合・export/unset行生成）の単体テスト。"""

from __future__ import annotations

import shlex

import pytest

from bswitch import shell

MCP_URL = "http://localhost:3333/mcp"


def _evaluated_exports(lines: list[str]) -> dict[str, str]:
    """export 行を POSIX シェルの字句規則で解釈し、変数名 → 値 の辞書にする。"""
    values: dict[str, str] = {}
    for line in lines:
        words = shlex.split(line)
        if words[0] == "export":
            for word in words[1:]:
                name, value = word.split("=", 1)
                values[name] = value
    return values


def test_shell_init_zsh_returns_function_definition() -> None:
    script = shell.shell_init("zsh")

    assert "bswitch()" in script
    assert "eval" in script
    assert "command bswitch" in script


def test_shell_init_unsupported_shell_raises_valueerror() -> None:
    with pytest.raises(ValueError, match="未対応のシェル"):
        shell.shell_init("bash")


def test_make_export_lines_single_project() -> None:
    """テストケース7: BACKLOG_API_KEY を unset し、BSWITCH_MCP_URL を export する。"""
    lines = shell.make_export_lines("test-space.backlog.com", "MY_PROJECT", MCP_URL)

    assert lines == [
        "unset BACKLOG_API_KEY",
        "export BACKLOG_SPACE=test-space.backlog.com",
        "export BACKLOG_DOMAIN=test-space.backlog.com",
        "export BACKLOG_PROJECT=MY_PROJECT",
        f"export BSWITCH_MCP_URL={MCP_URL}",
    ]


def test_make_export_lines_project_none_unsets_backlog_project() -> None:
    lines = shell.make_export_lines("test-space.backlog.com", None, MCP_URL)

    assert "unset BACKLOG_PROJECT" in lines
    assert not any(line.startswith("export BACKLOG_PROJECT") for line in lines)
    # 空文字での代替も不可（docs/design.md「--multi の仕様」）: unset行以外にBACKLOG_PROJECTへの言及がないこと。
    assert sum(1 for line in lines if "BACKLOG_PROJECT" in line) == 1


@pytest.mark.parametrize(
    "mcp_url",
    ["http://localhost:3333/mcp?a=1&b=2;$(touch x)`id`", "http://localhost:3333/it's mcp"],
)
def test_make_export_lines_mcp_url_with_shell_special_characters(mcp_url: str) -> None:
    """テストケース8: mcp_url にシェルの特殊文字があっても、eval すると値がそのまま入る。"""
    lines = shell.make_export_lines("space.example.com", "PROJ KEY", mcp_url)

    values = _evaluated_exports(lines)
    assert values["BSWITCH_MCP_URL"] == mcp_url
    assert values["BACKLOG_PROJECT"] == "PROJ KEY"


def test_make_unset_lines_unsets_all_variables() -> None:
    """テストケース15: BSWITCH_MCP_URL と BACKLOG_API_KEY を含めて unset する。"""
    lines = shell.make_unset_lines()

    assert lines == ["unset BACKLOG_API_KEY BACKLOG_SPACE BACKLOG_DOMAIN BACKLOG_PROJECT BSWITCH_MCP_URL"]
