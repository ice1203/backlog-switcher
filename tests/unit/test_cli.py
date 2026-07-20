"""cli.py の単体テスト。

BacklogClientは `cli.BacklogClient` をFakeBacklogClientへ差し替えて実HTTP通信を回避する。
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from bswitch import cli
from bswitch.models import Grant, State
from bswitch.state import load_state, save_state

from .conftest import FakeBacklogClient

MASTER_KEY_ENV = "BSWITCH_MASTER_API_KEY"

CONFIG_INI = """\
[default]
space = test-space.backlog.com
writer_user = 200
reader_user = 100
writer_api_key_ref = op://MyVault/backlog-svc-writer/credential
reader_api_key_ref = op://MyVault/backlog-svc-reader/credential

[profile test-read]
project = MY_PROJECT
permission = read

[profile test-write]
project = MY_PROJECT
permission = write
"""


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    path = tmp_path / "config"
    path.write_text(CONFIG_INI, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _common_env(monkeypatch: pytest.MonkeyPatch, config_path: Path) -> None:
    monkeypatch.setenv("BSWITCH_CONFIG", str(config_path))
    monkeypatch.setenv("BACKLOG_READER_API_KEY", "reader-dummy-key")
    monkeypatch.setenv("BACKLOG_WRITER_API_KEY", "writer-dummy-key")


def test_missing_master_key_exits_with_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(MASTER_KEY_ENV, raising=False)
    monkeypatch.setattr(sys, "argv", ["bswitch", "status"])

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert MASTER_KEY_ENV in captured.err


def test_bswitch_does_not_read_backlog_api_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """switch後の同一シェルでBACKLOG_API_KEYが残っていても、bswitchの動作は変化しない。"""
    monkeypatch.setenv(MASTER_KEY_ENV, "dummy-master-key")

    monkeypatch.delenv("BACKLOG_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["bswitch", "list"])
    cli.main()
    first_output = capsys.readouterr()

    monkeypatch.setenv("BACKLOG_API_KEY", "leftover-from-previous-switch")
    monkeypatch.setattr(sys, "argv", ["bswitch", "list"])
    cli.main()
    second_output = capsys.readouterr()

    assert first_output.err == second_output.err
    assert first_output.out == second_output.out


def test_list_shows_profiles(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv(MASTER_KEY_ENV, "dummy-master-key")
    monkeypatch.setattr(sys, "argv", ["bswitch", "list"])

    cli.main()

    captured = capsys.readouterr()
    assert "test-read" in captured.err
    assert "MY_PROJECT" in captured.err
    assert "read" in captured.err
    # eval対象のstdoutには何も出ないこと。
    assert captured.out == ""


def test_shell_init_does_not_require_master_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(MASTER_KEY_ENV, raising=False)
    monkeypatch.setattr(sys, "argv", ["bswitch", "shell-init", "zsh"])

    cli.main()  # 例外なく完了すること

    captured = capsys.readouterr()
    assert "bswitch()" in captured.out


def test_bare_bswitch_defaults_to_switch(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """引数なしの `bswitch` は `bswitch switch`（対話選択モード）として動作する。"""
    monkeypatch.setenv(MASTER_KEY_ENV, "dummy-master-key")
    fake = FakeBacklogClient()
    monkeypatch.setattr(cli, "BacklogClient", lambda *_a, **_kw: fake)
    monkeypatch.setattr(sys, "argv", ["bswitch"])

    monkeypatch.setattr(cli.ui, "select_profile", lambda profiles, **_kw: profiles[0])

    cli.main()

    captured = capsys.readouterr()
    assert "export BACKLOG_API_KEY=" in captured.out


def test_switch_end_to_end_with_fake_client(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(MASTER_KEY_ENV, "dummy-master-key")
    fake = FakeBacklogClient()
    monkeypatch.setattr(cli, "BacklogClient", lambda *_a, **_kw: fake)
    monkeypatch.setattr(sys, "argv", ["bswitch", "switch", "test-read"])

    cli.main()

    captured = capsys.readouterr()
    assert "export BACKLOG_API_KEY=reader-dummy-key" in captured.out
    assert "export BACKLOG_PROJECT=MY_PROJECT" in captured.out
    assert 100 in fake.members.get("MY_PROJECT", set())


def test_switch_write_uses_writer_key(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv(MASTER_KEY_ENV, "dummy-master-key")
    fake = FakeBacklogClient()
    monkeypatch.setattr(cli, "BacklogClient", lambda *_a, **_kw: fake)
    monkeypatch.setattr(sys, "argv", ["bswitch", "switch", "test-write"])

    cli.main()

    captured = capsys.readouterr()
    assert "export BACKLOG_API_KEY=writer-dummy-key" in captured.out


def test_switch_masks_api_key_when_stdout_is_a_tty(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """シェル統合を使わずターミナルへ直接出力する場合、APIキーの値は伏せて表示する。"""
    monkeypatch.setenv(MASTER_KEY_ENV, "dummy-master-key")
    fake = FakeBacklogClient()
    monkeypatch.setattr(cli, "BacklogClient", lambda *_a, **_kw: fake)
    monkeypatch.setattr(sys, "argv", ["bswitch", "switch", "test-read"])
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)

    cli.main()

    captured = capsys.readouterr()
    assert "export BACKLOG_API_KEY=********" in captured.out
    assert "reader-dummy-key" not in captured.out
    # APIキー以外のexport行は隠さない。
    assert "export BACKLOG_PROJECT=MY_PROJECT" in captured.out
    assert "シェル統合" in captured.err


def test_switch_shows_real_key_when_stdout_is_not_a_tty(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """シェル関数のeval経由（stdoutがtty以外）では、従来通り実際のキー値を出力する。"""
    monkeypatch.setenv(MASTER_KEY_ENV, "dummy-master-key")
    fake = FakeBacklogClient()
    monkeypatch.setattr(cli, "BacklogClient", lambda *_a, **_kw: fake)
    monkeypatch.setattr(sys, "argv", ["bswitch", "switch", "test-read"])
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)

    cli.main()

    captured = capsys.readouterr()
    assert "export BACKLOG_API_KEY=reader-dummy-key" in captured.out


def test_release_end_to_end_with_fake_client(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], config_path: Path
) -> None:
    """既定のreleaseはstate.jsonに記録された参加中プロジェクトから除名する。"""
    state_path = config_path.parent / "state.json"
    initial_state = State(
        grants=[Grant(profile="test-read", project="MY_PROJECT", user_id=100, permission="read", expires_at=None)]
    )
    save_state(state_path, initial_state)

    monkeypatch.setenv(MASTER_KEY_ENV, "dummy-master-key")
    fake = FakeBacklogClient()
    fake.members["MY_PROJECT"] = {100}
    monkeypatch.setattr(cli, "BacklogClient", lambda *_a, **_kw: fake)
    monkeypatch.setattr(sys, "argv", ["bswitch", "release"])

    cli.main()

    captured = capsys.readouterr()
    assert "unset BACKLOG_API_KEY BACKLOG_SPACE BACKLOG_DOMAIN BACKLOG_PROJECT" in captured.out
    assert fake.members["MY_PROJECT"] == set()
    assert load_state(state_path).grants == []


def test_release_default_without_grants_does_not_call_api(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """付与記録が0件の既定releaseはAPIを呼ばず、unset行のみ出力する。"""
    monkeypatch.setenv(MASTER_KEY_ENV, "dummy-master-key")
    fake = FakeBacklogClient()
    fake.members["MY_PROJECT"] = {100}  # 記録なしの残留参加（既定では触らない）
    monkeypatch.setattr(cli, "BacklogClient", lambda *_a, **_kw: fake)
    monkeypatch.setattr(sys, "argv", ["bswitch", "release"])

    cli.main()

    captured = capsys.readouterr()
    assert "unset BACKLOG_API_KEY BACKLOG_SPACE BACKLOG_DOMAIN BACKLOG_PROJECT" in captured.out
    assert fake.members["MY_PROJECT"] == {100}
    assert fake.call_log == []


def test_release_all_sweeps_config_projects_even_without_grants(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """release --all はstate.jsonに記録がなくてもconfig全プロジェクトを走査する（回復経路）。"""
    monkeypatch.setenv(MASTER_KEY_ENV, "dummy-master-key")
    fake = FakeBacklogClient()
    fake.members["MY_PROJECT"] = {100}
    monkeypatch.setattr(cli, "BacklogClient", lambda *_a, **_kw: fake)
    monkeypatch.setattr(sys, "argv", ["bswitch", "release", "--all"])

    cli.main()

    captured = capsys.readouterr()
    assert "unset BACKLOG_API_KEY BACKLOG_SPACE BACKLOG_DOMAIN BACKLOG_PROJECT" in captured.out
    assert fake.members["MY_PROJECT"] == set()


def test_delayed_enforcement_removes_expired_grant_on_enforce_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], config_path: Path
) -> None:
    state_path = config_path.parent / "state.json"
    past = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
    initial_state = State(
        grants=[Grant(profile="test-read", project="MY_PROJECT", user_id=100, permission="read", expires_at=past)]
    )
    save_state(state_path, initial_state)

    monkeypatch.setenv(MASTER_KEY_ENV, "dummy-master-key")
    fake = FakeBacklogClient()
    fake.members["MY_PROJECT"] = {100}
    monkeypatch.setattr(cli, "BacklogClient", lambda *_a, **_kw: fake)
    monkeypatch.setattr(sys, "argv", ["bswitch", "enforce"])

    cli.main()

    captured = capsys.readouterr()
    assert "期限切れの付与を1件解除しました" in captured.err
    reloaded = load_state(state_path)
    assert reloaded.grants == []
    assert fake.members["MY_PROJECT"] == set()


def test_delayed_enforcement_triggers_on_status_command_too(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], config_path: Path
) -> None:
    """遅延強制は enforce サブコマンド専用ではなく、任意のコマンド実行冒頭で走る。"""
    state_path = config_path.parent / "state.json"
    past = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
    initial_state = State(
        grants=[Grant(profile="test-read", project="MY_PROJECT", user_id=100, permission="read", expires_at=past)]
    )
    save_state(state_path, initial_state)

    monkeypatch.setenv(MASTER_KEY_ENV, "dummy-master-key")
    fake = FakeBacklogClient()
    fake.members["MY_PROJECT"] = {100}
    monkeypatch.setattr(cli, "BacklogClient", lambda *_a, **_kw: fake)
    monkeypatch.setattr(sys, "argv", ["bswitch", "status"])

    cli.main()

    captured = capsys.readouterr()
    assert "期限切れの付与を1件解除しました" in captured.err
    assert load_state(state_path).grants == []


# --- bswitch check のテスト ---


def test_check_no_grants_returns_empty_array(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["bswitch", "check"])

    cli.main()

    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == []


def test_check_read_grant_matching_key_returns_ok(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], config_path: Path
) -> None:
    state_path = config_path.parent / "state.json"
    save_state(
        state_path,
        State(
            grants=[Grant(profile="test-read", project="MY_PROJECT", user_id=100, permission="read", expires_at=None)]
        ),
    )
    monkeypatch.setenv("BACKLOG_API_KEY", "reader-dummy-key")
    monkeypatch.setattr(sys, "argv", ["bswitch", "check"])

    cli.main()

    result = json.loads(capsys.readouterr().err)
    assert result == [{"profile": "test-read", "permission": "read", "status": "OK"}]


def test_check_read_grant_mismatched_key_returns_mismatch(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], config_path: Path
) -> None:
    state_path = config_path.parent / "state.json"
    save_state(
        state_path,
        State(
            grants=[Grant(profile="test-read", project="MY_PROJECT", user_id=100, permission="read", expires_at=None)]
        ),
    )
    monkeypatch.setenv("BACKLOG_API_KEY", "wrong-key")
    monkeypatch.setattr(sys, "argv", ["bswitch", "check"])

    cli.main()

    result = json.loads(capsys.readouterr().err)
    assert result == [{"profile": "test-read", "permission": "read", "status": "MISMATCH"}]


def test_check_no_backlog_api_key_returns_not_set(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], config_path: Path
) -> None:
    state_path = config_path.parent / "state.json"
    save_state(
        state_path,
        State(
            grants=[Grant(profile="test-read", project="MY_PROJECT", user_id=100, permission="read", expires_at=None)]
        ),
    )
    monkeypatch.delenv("BACKLOG_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["bswitch", "check"])

    cli.main()

    result = json.loads(capsys.readouterr().err)
    assert result == [{"profile": "test-read", "permission": "read", "status": "NOT_SET"}]


def test_check_write_grant_matching_key_returns_ok(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], config_path: Path
) -> None:
    state_path = config_path.parent / "state.json"
    save_state(
        state_path,
        State(
            grants=[Grant(profile="test-write", project="MY_PROJECT", user_id=200, permission="write", expires_at=None)]
        ),
    )
    monkeypatch.setenv("BACKLOG_API_KEY", "writer-dummy-key")
    monkeypatch.setattr(sys, "argv", ["bswitch", "check"])

    cli.main()

    result = json.loads(capsys.readouterr().err)
    assert result == [{"profile": "test-write", "permission": "write", "status": "OK"}]


def test_check_works_without_master_api_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(MASTER_KEY_ENV, raising=False)
    monkeypatch.setattr(sys, "argv", ["bswitch", "check"])

    cli.main()

    assert json.loads(capsys.readouterr().err) == []


def test_check_op_read_failure_exits_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], config_path: Path
) -> None:
    from bswitch import keys as bswitch_keys
    from bswitch.keys import KeyResolutionError

    state_path = config_path.parent / "state.json"
    save_state(
        state_path,
        State(
            grants=[Grant(profile="test-read", project="MY_PROJECT", user_id=100, permission="read", expires_at=None)]
        ),
    )
    monkeypatch.delenv("BACKLOG_READER_API_KEY", raising=False)

    def fail_op_read(*args: object, **kwargs: object) -> str:
        raise KeyResolutionError("op read失敗")

    monkeypatch.setattr(bswitch_keys, "_read_from_1password", fail_op_read)
    monkeypatch.setattr(sys, "argv", ["bswitch", "check"])

    with pytest.raises(SystemExit) as excinfo:
        cli.main()

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "エラー" in captured.err
