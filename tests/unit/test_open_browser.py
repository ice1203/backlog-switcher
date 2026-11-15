"""open_browser.py（`bswitch-open-browser`: 認可画面の振り分け）の単体テスト。

`open` コマンドは実行しない。`bswitch.open_browser.subprocess.run` をモックし、渡された引数を確かめる。
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pytest_mock import MockerFixture

from bswitch import open_browser
from bswitch.models import Grant, State
from bswitch.state import save_state

SPACE = "test-space.backlog.com"

CONFIG_INI = f"""\
[default]
space = {SPACE}
writer_user = 200
reader_user = 100
writer_browser_profile = Profile 2
reader_browser_profile = Profile 3

[profile test-read]
project = MY_PROJECT
permission = read

[profile test-write]
project = MY_PROJECT
permission = write

[profile test-admin]
project = MY_PROJECT
permission = admin
"""


@pytest.fixture
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config"
    path.write_text(CONFIG_INI, encoding="utf-8")
    monkeypatch.setenv("BSWITCH_CONFIG", str(path))
    monkeypatch.delenv("BSWITCH_MASTER_API_KEY", raising=False)
    return path


@pytest.fixture
def run_mock(mocker: MockerFixture) -> Any:
    return mocker.patch(
        "bswitch.open_browser.subprocess.run",
        return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
    )


def _save_grants(config_path: Path, *grants: Grant) -> None:
    save_state(config_path.parent / "state.json", State(grants=list(grants)))


def _grant(permission: str, *, expires_at: str | None = None, project: str = "MY_PROJECT") -> Grant:
    return Grant(
        profile=f"test-{permission}",
        project=project,
        user_id=100 if permission == "read" else 200,
        permission=permission,
        expires_at=expires_at,
    )


def _run(argv: list[str]) -> int:
    with pytest.raises(SystemExit) as excinfo:
        open_browser.main(argv)
    code = excinfo.value.code
    return 0 if code is None else int(code)


def _chrome_args(profile: str, url: str) -> list[str]:
    return ["open", "-na", "Google Chrome", "--args", f"--profile-directory={profile}", url]


def _opened_args(run_mock: Any) -> list[str]:
    run_mock.assert_called_once()
    return list(run_mock.call_args.args[0])


# ---------------------------------------------------------------------------
# 仮想ユーザーのプロファイルで開く (17〜21)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("permission", ["write", "admin"])
def test_writer_grant_opens_space_url_with_writer_profile(config_path: Path, run_mock: Any, permission: str) -> None:
    """テストケース17: write / admin の付与があるとき、space の URL を writer_browser_profile で開く。"""
    _save_grants(config_path, _grant(permission))
    url = f"https://{SPACE}/OAuth2AccessRequest.action?client_id=abc"

    assert _run([url]) == 0

    assert _opened_args(run_mock) == _chrome_args("Profile 2", url)


def test_reader_grant_opens_space_url_with_reader_profile(config_path: Path, run_mock: Any) -> None:
    """テストケース18: read の付与があるとき、space の URL を reader_browser_profile で開く。"""
    _save_grants(config_path, _grant("read"))
    url = f"https://{SPACE}/"

    assert _run([url]) == 0

    assert _opened_args(run_mock) == _chrome_args("Profile 3", url)


def test_mcp_server_url_with_other_path_opens_with_virtual_user_profile(config_path: Path, run_mock: Any) -> None:
    """テストケース19: mcp_url とスキーム・ホスト・ポートが同じでパスが違う URL を仮想ユーザーのプロファイルで開く。"""
    _save_grants(config_path, _grant("write"))
    url = "http://localhost:3333/authorize?response_type=code&client_id=abc&redirect_uri=http%3A%2F%2Flocalhost"

    assert _run([url]) == 0

    assert _opened_args(run_mock) == _chrome_args("Profile 2", url)


@pytest.mark.parametrize(
    "url",
    [
        "https://TEST-SPACE.Backlog.COM:443/",
        "http://LOCALHOST:3333/authorize?response_type=code&client_id=abc",
    ],
)
def test_host_case_and_default_port_are_treated_as_same(config_path: Path, run_mock: Any, url: str) -> None:
    """テストケース20: ホストの大文字・小文字の違いと、省略時のポートを一致と扱う。"""
    _save_grants(config_path, _grant("read"))

    assert _run([url]) == 0

    assert _opened_args(run_mock) == _chrome_args("Profile 3", url)


def test_default_port_of_mcp_url_is_filled_in(config_path: Path, run_mock: Any) -> None:
    """テストケース20: mcp_url のポート省略 (https → 443) と、URL の明示的な 443 を一致と扱う。"""
    config_path.write_text(
        CONFIG_INI.replace("reader_user = 100\n", "reader_user = 100\nmcp_url = https://mcp.example.com/mcp\n"),
        encoding="utf-8",
    )
    _save_grants(config_path, _grant("read"))
    url = "https://mcp.example.com:443/authorize?response_type=code&client_id=abc"

    assert _run([url]) == 0

    assert _opened_args(run_mock) == _chrome_args("Profile 3", url)


def test_url_with_different_port_is_not_treated_as_mcp_server(config_path: Path, run_mock: Any) -> None:
    """テストケース21: ポートだけが違う URL (http://localhost:3334/...) は MCP サーバーとして扱わない。"""
    _save_grants(config_path, _grant("write"))
    url = "http://localhost:3334/index.html"

    assert _run([url]) == 0

    assert _opened_args(run_mock) == ["open", url]


# ---------------------------------------------------------------------------
# 付与がない・設定が足りないときは開かない (22〜27)
# ---------------------------------------------------------------------------

_TARGET_URLS = [
    f"https://{SPACE}/OAuth2AccessRequest.action",
    "http://localhost:3333/authorize?response_type=code&client_id=abc",
]


@pytest.mark.parametrize("url", _TARGET_URLS)
def test_no_active_grants_does_not_open_and_guides_switch(
    config_path: Path, run_mock: Any, capsys: pytest.CaptureFixture[str], url: str
) -> None:
    """テストケース22 / 23: 期限内の付与がないとき、開かず exit 1 になり、stderr で bswitch switch を案内する。

    期限切れの判定 (期限ちょうどを含む) は test_switcher の active_grants のテストで確かめる。
    """
    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    _save_grants(config_path, _grant("write", expires_at=past))

    assert _run([url]) == 1

    run_mock.assert_not_called()
    assert "bswitch switch" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("permission", "key"),
    [("read", "reader_browser_profile"), ("write", "writer_browser_profile")],
)
def test_missing_browser_profile_does_not_open_and_names_the_key(
    config_path: Path, run_mock: Any, capsys: pytest.CaptureFixture[str], permission: str, key: str
) -> None:
    """テストケース24: 付与中の権限のプロファイルが config にないとき、開かずに exit 1 になり、項目名を案内する。"""
    config_path.write_text(
        "\n".join(line for line in CONFIG_INI.splitlines() if not line.startswith(key)) + "\n", encoding="utf-8"
    )
    _save_grants(config_path, _grant(permission))

    assert _run([f"https://{SPACE}/"]) == 1

    run_mock.assert_not_called()
    assert key in capsys.readouterr().err


def test_mixed_reader_and_writer_grants_do_not_open(
    config_path: Path, run_mock: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """テストケース25: reader と writer の付与が混ざっているとき、開かずに exit 1 になる。"""
    _save_grants(config_path, _grant("read"), _grant("write", project="OTHER"))

    assert _run([f"https://{SPACE}/"]) == 1

    run_mock.assert_not_called()
    assert capsys.readouterr().err != ""


@pytest.mark.parametrize("content", [None, "{not json"])
def test_missing_or_broken_state_does_not_open(
    config_path: Path, run_mock: Any, capsys: pytest.CaptureFixture[str], content: str | None
) -> None:
    """テストケース26: state.json がない・壊れているとき、22 と同じになる。"""
    if content is not None:
        (config_path.parent / "state.json").write_text(content, encoding="utf-8")

    assert _run([f"https://{SPACE}/"]) == 1

    run_mock.assert_not_called()
    assert "bswitch switch" in capsys.readouterr().err


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:3333/authorize?response_type=code&client_id=abc",
        "http://[::1]:3333/authorize?response_type=code&client_id=abc",
        "http://localhost:4444/authorize?client_id=abc&response_type=code",
    ],
)
def test_loopback_authorization_request_not_matching_mcp_url_does_not_open(
    config_path: Path, run_mock: Any, capsys: pytest.CaptureFixture[str], url: str
) -> None:
    """テストケース27: ループバックの OAuth の認可要求で mcp_url と一致しない URL は、開かずに exit 1 になる。"""
    _save_grants(config_path, _grant("write"))

    assert _run([url]) == 1

    run_mock.assert_not_called()
    assert "mcp_url" in capsys.readouterr().err


def test_loopback_url_without_authorization_query_is_opened_normally(config_path: Path, run_mock: Any) -> None:
    """ループバックでも OAuth の認可要求でない URL (client_id がない) は既定のブラウザで開く。"""
    url = "http://127.0.0.1:8080/?response_type=code"

    assert _run([url]) == 0

    assert _opened_args(run_mock) == ["open", url]


# ---------------------------------------------------------------------------
# それ以外の URL・異常系 (28〜33)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("with_grant", [True, False])
def test_unrelated_url_is_opened_with_default_browser(config_path: Path, run_mock: Any, with_grant: bool) -> None:
    """テストケース28: 関係ない URL は、付与の有無に関係なく open <URL> で開く。"""
    if with_grant:
        _save_grants(config_path, _grant("write"))
    url = "https://github.com/nulab/backlog-mcp-server"

    assert _run([url]) == 0

    assert _opened_args(run_mock) == ["open", url]


@pytest.mark.parametrize("content", [None, "[default]\nwriter_user = 200\n", "not an ini"])
def test_missing_or_invalid_config_does_not_open_any_url(
    config_path: Path, run_mock: Any, capsys: pytest.CaptureFixture[str], content: str | None
) -> None:
    """テストケース29: config がない・不正なとき、どの URL も開かずに exit 1 になる。"""
    if content is None:
        config_path.unlink()
    else:
        config_path.write_text(content, encoding="utf-8")

    assert _run(["https://github.com/"]) == 1

    run_mock.assert_not_called()
    assert capsys.readouterr().err != ""


@pytest.mark.parametrize("argv", [[], ["https://a.example.com/", "https://b.example.com/"]])
def test_wrong_number_of_arguments_shows_usage_and_exits_2(
    config_path: Path, run_mock: Any, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    """テストケース30: 引数がない、または2つ以上のときは、使い方を表示して exit 2 になる。"""
    assert _run(argv) == 2

    run_mock.assert_not_called()
    assert "bswitch-open-browser" in capsys.readouterr().err


def test_open_failure_exits_with_nonzero_code(config_path: Path, mocker: MockerFixture) -> None:
    """テストケース31: open が失敗したとき (Chrome がないなど) は 0 以外で終了する (理由は open 自身が出す)。"""
    _save_grants(config_path, _grant("write"))
    mocker.patch(
        "bswitch.open_browser.subprocess.run",
        return_value=subprocess.CompletedProcess(args=[], returncode=1),
    )

    assert _run([f"https://{SPACE}/"]) == 1
