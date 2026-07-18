"""shell.py（シェル統合・export/unset行生成）の単体テスト。"""

from __future__ import annotations

import pytest

from bswitch import shell


def test_shell_init_zsh_returns_function_definition() -> None:
    script = shell.shell_init("zsh")

    assert "bswitch()" in script
    assert "eval" in script
    assert "command bswitch" in script


def test_shell_init_unsupported_shell_raises_valueerror() -> None:
    with pytest.raises(ValueError, match="未対応のシェル"):
        shell.shell_init("bash")


def test_make_export_lines_single_project_contains_all_four_variables() -> None:
    lines = shell.make_export_lines("dummy-key-value", "test-space.backlog.com", "MY_PROJECT")

    assert "export BACKLOG_API_KEY=dummy-key-value" in lines
    assert "export BACKLOG_SPACE=https://test-space.backlog.com" in lines
    assert "export BACKLOG_DOMAIN=test-space.backlog.com" in lines
    assert "export BACKLOG_PROJECT=MY_PROJECT" in lines
    assert len(lines) == 4


def test_make_export_lines_project_none_unsets_backlog_project() -> None:
    lines = shell.make_export_lines("dummy-key-value", "test-space.backlog.com", None)

    assert "unset BACKLOG_PROJECT" in lines
    assert not any(line.startswith("export BACKLOG_PROJECT") for line in lines)
    # 空文字での代替も不可（docs/design.md「--multi の仕様」）: unset行以外にBACKLOG_PROJECTへの言及がないこと。
    assert sum(1 for line in lines if "BACKLOG_PROJECT" in line) == 1


def test_make_export_lines_quotes_values_with_special_characters() -> None:
    lines = shell.make_export_lines("value with space", "space.example.com", "PROJ KEY")

    assert "export BACKLOG_API_KEY='value with space'" in lines
    assert "export BACKLOG_PROJECT='PROJ KEY'" in lines


def test_make_unset_lines_unsets_all_four_variables() -> None:
    lines = shell.make_unset_lines()

    assert lines == ["unset BACKLOG_API_KEY BACKLOG_SPACE BACKLOG_DOMAIN BACKLOG_PROJECT"]
