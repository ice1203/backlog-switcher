"""switcher.py（排他動作・TTL・enforce）の単体テスト。

実API・実サブプロセスは呼ばない。BacklogClientはconftest.pyの `FakeBacklogClient` で置き換える。

TTL・遅延強制のテストは、実際に待機したり `datetime` をモックする代わりに
「過去のISO8601文字列を持つGrantを直接構築する」方式で期限切れ状態を再現する。
switcher.pyは `datetime.now(UTC)` との比較でしか期限切れ判定をしないため、
この方式は時刻モックと同等の効果があり、より壊れにくい。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from bswitch import switcher
from bswitch.models import DefaultConfig, Grant, Profile, State

from .conftest import FakeBacklogClient

#: switch()内のresolve_api_key呼び出しがsubprocess(op)を叩かないよう、
#: 全テストで環境変数フォールバックのダミーキーを注入する。
WRITER_KEY = "writer-dummy-key"
READER_KEY = "reader-dummy-key"


@pytest.fixture(autouse=True)
def _api_key_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BACKLOG_WRITER_API_KEY", WRITER_KEY)
    monkeypatch.setenv("BACKLOG_READER_API_KEY", READER_KEY)


# ---------------------------------------------------------------------------
# switch(): 排他動作（除名→参加の呼び出し順序）
# ---------------------------------------------------------------------------


def test_switch_exclusive_removal_precedes_add(
    fake_client: FakeBacklogClient, numeric_config: DefaultConfig, state: State
) -> None:
    profile_a = Profile(name="a", project="PROJ_A", permission="read")
    profile_b = Profile(name="b", project="PROJ_B", permission="write")
    all_profiles = [profile_a, profile_b]

    lines, overall = switcher.switch([profile_a], all_profiles, numeric_config, None, fake_client, state)

    expected_removal_phase = [
        ("delete_project_user", "PROJ_A", 200),
        ("delete_project_user", "PROJ_A", 100),
        ("delete_project_administrator", "PROJ_A", 200),
        ("delete_project_administrator", "PROJ_A", 100),
        ("delete_project_user", "PROJ_B", 200),
        ("delete_project_user", "PROJ_B", 100),
        ("delete_project_administrator", "PROJ_B", 200),
        ("delete_project_administrator", "PROJ_B", 100),
    ]
    assert fake_client.call_log[: len(expected_removal_phase)] == expected_removal_phase

    # 参加操作（対象プロジェクトへのadd）は除名フェーズより後に発生する。
    add_index = fake_client.call_log.index(("add_project_user", "PROJ_A", 100))
    assert add_index >= len(expected_removal_phase)

    assert overall == "read"
    assert len(state.grants) == 1
    grant = state.grants[0]
    assert (grant.profile, grant.project, grant.user_id, grant.permission) == ("a", "PROJ_A", 100, "read")
    # --duration/default_duration未指定時はプログラム既定値(8h)が適用される(無期限にはならない)。
    expires = datetime.fromisoformat(grant.expires_at)  # type: ignore[arg-type]
    assert timedelta(hours=7, minutes=55) < expires - datetime.now(UTC) <= timedelta(hours=8, minutes=1)
    assert f"export BACKLOG_API_KEY={READER_KEY}" in lines


def test_switch_only_removes_from_config_defined_projects(
    fake_client: FakeBacklogClient, numeric_config: DefaultConfig, state: State
) -> None:
    """config定義外プロジェクトには操作しない（brief禁止事項）ことをall_profiles経由で確認する。"""
    profile_a = Profile(name="a", project="PROJ_A", permission="write")
    all_profiles = [profile_a]

    switcher.switch([profile_a], all_profiles, numeric_config, None, fake_client, state)

    touched_projects = {call[1] for call in fake_client.call_log if len(call) > 1}
    assert touched_projects == {"PROJ_A"}


# ---------------------------------------------------------------------------
# --multi: read と write/admin の混在バリデーション
# ---------------------------------------------------------------------------


def test_multi_mixed_read_and_write_raises_and_performs_no_operations(
    fake_client: FakeBacklogClient, numeric_config: DefaultConfig, state: State
) -> None:
    profile_read = Profile(name="r", project="P1", permission="read")
    profile_write = Profile(name="w", project="P2", permission="write")

    with pytest.raises(switcher.SwitcherError):
        switcher.switch(
            [profile_read, profile_write],
            [profile_read, profile_write],
            numeric_config,
            None,
            fake_client,
            state,
        )

    assert fake_client.call_log == []
    assert state.grants == []


def test_multi_mixed_read_and_admin_raises(
    fake_client: FakeBacklogClient, numeric_config: DefaultConfig, state: State
) -> None:
    profile_read = Profile(name="r", project="P1", permission="read")
    profile_admin = Profile(name="ad", project="P2", permission="admin")

    with pytest.raises(switcher.SwitcherError):
        switcher.switch(
            [profile_read, profile_admin],
            [profile_read, profile_admin],
            numeric_config,
            None,
            fake_client,
            state,
        )

    assert fake_client.call_log == []


def test_multi_write_and_admin_mixed_is_allowed(
    fake_client: FakeBacklogClient, numeric_config: DefaultConfig, state: State
) -> None:
    profile_write = Profile(name="w", project="P1", permission="write")
    profile_admin = Profile(name="ad", project="P2", permission="admin")

    lines, overall = switcher.switch(
        [profile_write, profile_admin],
        [profile_write, profile_admin],
        numeric_config,
        None,
        fake_client,
        state,
    )

    assert overall == "admin"
    assert {g.project for g in state.grants} == {"P1", "P2"}
    assert all(g.user_id == 200 for g in state.grants)  # どちらもwriter
    assert f"export BACKLOG_API_KEY={WRITER_KEY}" in lines


# ---------------------------------------------------------------------------
# --multi: BACKLOG_PROJECT の unset / export
# ---------------------------------------------------------------------------


def test_multi_two_or_more_profiles_unsets_backlog_project(
    fake_client: FakeBacklogClient, numeric_config: DefaultConfig, state: State
) -> None:
    profile_a = Profile(name="a", project="P1", permission="write")
    profile_b = Profile(name="b", project="P2", permission="write")

    lines, _ = switcher.switch([profile_a, profile_b], [profile_a, profile_b], numeric_config, None, fake_client, state)

    assert "unset BACKLOG_PROJECT" in lines
    assert not any(line.startswith("export BACKLOG_PROJECT") for line in lines)


def test_multi_single_selection_sets_backlog_project(
    fake_client: FakeBacklogClient, numeric_config: DefaultConfig, state: State
) -> None:
    profile = Profile(name="w", project="P1", permission="write")

    lines, _ = switcher.switch([profile], [profile], numeric_config, None, fake_client, state)

    assert "export BACKLOG_PROJECT=P1" in lines


# ---------------------------------------------------------------------------
# admin → write 縮退
# ---------------------------------------------------------------------------


def test_admin_degrades_to_write_on_permission_error(
    fake_client: FakeBacklogClient, numeric_config: DefaultConfig, state: State, capsys: pytest.CaptureFixture[str]
) -> None:
    fake_client.admin_denied_projects.add("P1")
    profile = Profile(name="ad", project="P1", permission="admin")

    lines, overall = switcher.switch([profile], [profile], numeric_config, None, fake_client, state)

    assert overall == "write"
    assert state.grants[0].permission == "write"
    assert state.grants[0].user_id == 200  # writerのまま
    captured = capsys.readouterr()
    assert "警告" in captured.err
    assert "write" in captured.err
    # 異常終了しないこと（例外が飛ばず正常にexport行が返る）。
    assert f"export BACKLOG_API_KEY={WRITER_KEY}" in lines


def test_admin_succeeds_when_not_denied(
    fake_client: FakeBacklogClient, numeric_config: DefaultConfig, state: State, capsys: pytest.CaptureFixture[str]
) -> None:
    profile = Profile(name="ad", project="P1", permission="admin")

    switcher.switch([profile], [profile], numeric_config, None, fake_client, state)

    assert state.grants[0].permission == "admin"
    assert 200 in fake_client.admins.get("P1", set())
    assert "警告" not in capsys.readouterr().err


# ---------------------------------------------------------------------------
# メール→ID解決とキャッシュ
# ---------------------------------------------------------------------------


def _email_config(default_duration: str | None = None) -> DefaultConfig:
    return DefaultConfig(
        space="test-space.backlog.com",
        writer_user="writer@example.com",
        reader_user="reader@example.com",
        writer_api_key_ref="op://MyVault/backlog-svc-writer/credential",
        reader_api_key_ref="op://MyVault/backlog-svc-reader/credential",
        default_duration=default_duration,
    )


def test_email_resolution_cache_miss_then_hit(fake_client: FakeBacklogClient, state: State) -> None:
    config = _email_config()
    fake_client.users = [
        {"id": 200, "mailAddress": "writer@example.com", "name": "writer"},
        {"id": 100, "mailAddress": "reader@example.com", "name": "reader"},
    ]
    profile = Profile(name="w", project="P1", permission="write")

    switcher.switch([profile], [profile], config, None, fake_client, state)
    assert fake_client.get_users_calls == 1
    assert state.user_id_cache == {"writer@example.com": 200, "reader@example.com": 100}

    # 2回目はキャッシュヒットでget_usersを呼ばない。
    switcher.switch([profile], [profile], config, None, fake_client, state)
    assert fake_client.get_users_calls == 1


def test_numeric_id_direct_specification_skips_user_lookup(
    fake_client: FakeBacklogClient, numeric_config: DefaultConfig, state: State
) -> None:
    profile = Profile(name="w", project="P1", permission="write")

    switcher.switch([profile], [profile], numeric_config, None, fake_client, state)

    assert fake_client.get_users_calls == 0


def test_email_resolution_failure_raises_switchererror(fake_client: FakeBacklogClient, state: State) -> None:
    config = _email_config()
    fake_client.users = []  # 該当するメールアドレスのユーザーが存在しない
    profile = Profile(name="w", project="P1", permission="write")

    with pytest.raises(switcher.SwitcherError):
        switcher.switch([profile], [profile], config, None, fake_client, state)


# ---------------------------------------------------------------------------
# TTL（--duration / default_duration）
# ---------------------------------------------------------------------------


def test_duration_recorded_in_state(
    fake_client: FakeBacklogClient, numeric_config: DefaultConfig, state: State
) -> None:
    profile = Profile(name="w", project="P1", permission="write")
    before = datetime.now(UTC)

    switcher.switch([profile], [profile], numeric_config, "30s", fake_client, state)

    after = datetime.now(UTC)
    grant = state.grants[0]
    assert grant.expires_at is not None
    expires = datetime.fromisoformat(grant.expires_at)
    assert before + timedelta(seconds=25) <= expires <= after + timedelta(seconds=35)


def test_no_duration_and_no_default_duration_falls_back_to_8h(
    fake_client: FakeBacklogClient, numeric_config: DefaultConfig, state: State
) -> None:
    """--duration も default_duration も未指定の場合、無期限ではなくプログラム既定値(8h)が適用される。"""
    profile = Profile(name="w", project="P1", permission="write")

    switcher.switch([profile], [profile], numeric_config, None, fake_client, state)

    expires = datetime.fromisoformat(state.grants[0].expires_at)  # type: ignore[arg-type]
    delta = expires - datetime.now(UTC)
    assert timedelta(hours=7, minutes=55) < delta <= timedelta(hours=8, minutes=1)


def test_default_duration_applied_when_duration_not_specified(fake_client: FakeBacklogClient, state: State) -> None:
    config = DefaultConfig(
        space="test-space.backlog.com",
        writer_user="200",
        reader_user="100",
        writer_api_key_ref="op://MyVault/backlog-svc-writer/credential",
        reader_api_key_ref="op://MyVault/backlog-svc-reader/credential",
        default_duration="8h",
    )
    profile = Profile(name="w", project="P1", permission="write")

    switcher.switch([profile], [profile], config, None, fake_client, state)

    expires = datetime.fromisoformat(state.grants[0].expires_at)  # type: ignore[arg-type]
    delta = expires - datetime.now(UTC)
    assert timedelta(hours=7, minutes=55) < delta <= timedelta(hours=8, minutes=1)


def test_explicit_duration_overrides_default_duration(fake_client: FakeBacklogClient, state: State) -> None:
    config = DefaultConfig(
        space="test-space.backlog.com",
        writer_user="200",
        reader_user="100",
        writer_api_key_ref="op://MyVault/backlog-svc-writer/credential",
        reader_api_key_ref="op://MyVault/backlog-svc-reader/credential",
        default_duration="8h",
    )
    profile = Profile(name="w", project="P1", permission="write")

    switcher.switch([profile], [profile], config, "30s", fake_client, state)

    expires = datetime.fromisoformat(state.grants[0].expires_at)  # type: ignore[arg-type]
    delta = expires - datetime.now(UTC)
    assert delta < timedelta(minutes=1)


def test_invalid_duration_format_raises_valueerror(
    fake_client: FakeBacklogClient, numeric_config: DefaultConfig, state: State
) -> None:
    profile = Profile(name="w", project="P1", permission="write")

    with pytest.raises(ValueError, match="不正な期間形式"):
        switcher.switch([profile], [profile], numeric_config, "1d", fake_client, state)

    # duration検証は排他除名より前（fail fast）のためAPI呼び出しは発生しない。
    assert fake_client.call_log == []


@pytest.mark.parametrize(
    ("value", "expected_seconds"),
    [("30s", 30), ("30m", 1800), ("2h", 7200), ("8h", 28800)],
)
def test_parse_duration(value: str, expected_seconds: int) -> None:
    assert switcher.parse_duration(value) == expected_seconds


# ---------------------------------------------------------------------------
# 遅延強制 / enforce: 期限切れGrantの自動解除
# ---------------------------------------------------------------------------


def test_enforce_expired_removes_only_expired_grants(
    fake_client: FakeBacklogClient, numeric_config: DefaultConfig
) -> None:
    past = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    expired_grant = Grant(profile="a", project="P1", user_id=100, permission="read", expires_at=past)
    active_grant = Grant(profile="b", project="P2", user_id=200, permission="write", expires_at=future)
    state = State(grants=[expired_grant, active_grant])
    all_profiles = [
        Profile(name="a", project="P1", permission="read"),
        Profile(name="b", project="P2", permission="write"),
    ]

    removed = switcher.enforce_expired(numeric_config, all_profiles, fake_client, state)

    assert removed == 1
    assert state.grants == [active_grant]
    assert ("delete_project_user", "P1", 100) in fake_client.call_log
    assert not any(call[0] == "delete_project_user" and call[1] == "P2" for call in fake_client.call_log)


def test_enforce_expired_admin_grant_also_removes_administrator(
    fake_client: FakeBacklogClient, numeric_config: DefaultConfig
) -> None:
    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    grant = Grant(profile="a", project="P1", user_id=200, permission="admin", expires_at=past)
    state = State(grants=[grant])
    all_profiles = [Profile(name="a", project="P1", permission="admin")]

    removed = switcher.enforce_expired(numeric_config, all_profiles, fake_client, state)

    assert removed == 1
    assert ("delete_project_user", "P1", 200) in fake_client.call_log
    assert ("delete_project_administrator", "P1", 200) in fake_client.call_log


def test_enforce_expired_skips_project_not_in_current_config(
    fake_client: FakeBacklogClient, numeric_config: DefaultConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    """configから該当プロファイルが削除された場合、config定義外プロジェクトには操作しない。"""
    past = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    grant = Grant(profile="removed", project="GONE", user_id=100, permission="read", expires_at=past)
    state = State(grants=[grant])

    removed = switcher.enforce_expired(numeric_config, [], fake_client, state)

    assert removed == 1
    assert state.grants == []
    assert fake_client.call_log == []  # API操作は一切発生しない
    assert "GONE" in capsys.readouterr().err


def test_enforce_expired_noop_when_nothing_expired(
    fake_client: FakeBacklogClient, numeric_config: DefaultConfig
) -> None:
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    grant = Grant(profile="a", project="P1", user_id=100, permission="read", expires_at=future)
    state = State(grants=[grant])
    all_profiles = [Profile(name="a", project="P1", permission="read")]

    removed = switcher.enforce_expired(numeric_config, all_profiles, fake_client, state)

    assert removed == 0
    assert state.grants == [grant]
    assert fake_client.call_log == []


def test_enforce_expired_returns_zero_when_no_grants(
    fake_client: FakeBacklogClient, numeric_config: DefaultConfig, state: State
) -> None:
    removed = switcher.enforce_expired(numeric_config, [], fake_client, state)

    assert removed == 0
    assert fake_client.call_log == []


# ---------------------------------------------------------------------------
# 冪等性
# ---------------------------------------------------------------------------


def test_switch_twice_in_a_row_does_not_raise(
    fake_client: FakeBacklogClient, numeric_config: DefaultConfig, state: State
) -> None:
    profile = Profile(name="w", project="P1", permission="write")

    switcher.switch([profile], [profile], numeric_config, None, fake_client, state)
    # 2回目の実行でも例外が発生しない（除名→再参加のサイクルが安全に繰り返せる）。
    switcher.switch([profile], [profile], numeric_config, None, fake_client, state)

    assert state.grants[0].project == "P1"
    assert 200 in fake_client.members["P1"]


def test_release_idempotent_when_nobody_is_participating(
    fake_client: FakeBacklogClient, numeric_config: DefaultConfig
) -> None:
    all_profiles = [Profile(name="w", project="P1", permission="write")]
    state = State()

    lines = switcher.release(numeric_config, all_profiles, fake_client, state)

    assert state.grants == []
    assert lines == ["unset BACKLOG_API_KEY BACKLOG_SPACE BACKLOG_DOMAIN BACKLOG_PROJECT"]


def test_release_removes_participating_users(
    fake_client: FakeBacklogClient, numeric_config: DefaultConfig, state: State
) -> None:
    profile = Profile(name="w", project="P1", permission="write")
    switcher.switch([profile], [profile], numeric_config, None, fake_client, state)
    assert fake_client.members["P1"] == {200}

    switcher.release(numeric_config, [profile], fake_client, state)

    assert fake_client.members["P1"] == set()
    assert state.grants == []


def test_add_user_idempotent_skips_call_when_already_member(fake_client: FakeBacklogClient) -> None:
    fake_client.members["P1"] = {100}

    switcher._add_user_idempotent(fake_client, "P1", 100)  # noqa: SLF001 - 内部関数の直接検証

    assert not any(call[0] == "add_project_user" for call in fake_client.call_log)


def test_add_user_idempotent_calls_add_when_not_member(fake_client: FakeBacklogClient) -> None:
    switcher._add_user_idempotent(fake_client, "P1", 100)  # noqa: SLF001

    assert ("add_project_user", "P1", 100) in fake_client.call_log


def test_exclusive_remove_does_not_raise_when_nobody_is_member(fake_client: FakeBacklogClient) -> None:
    # 誰も参加していない状態での除名（未参加ユーザーの除名）が例外なく完了すること。
    switcher._exclusive_remove(fake_client, ["P1", "P2"], 200, 100)  # noqa: SLF001

    assert len(fake_client.call_log) == 8  # 2プロジェクト x (user2 + admin2)


# ---------------------------------------------------------------------------
# permission=read→reader_user, write/admin→writer_user の対応
# ---------------------------------------------------------------------------


def test_permission_maps_to_correct_virtual_user(
    fake_client: FakeBacklogClient, numeric_config: DefaultConfig, state: State
) -> None:
    read_profile = Profile(name="r", project="P1", permission="read")
    switcher.switch([read_profile], [read_profile], numeric_config, None, fake_client, state)
    assert state.grants[0].user_id == 100  # reader

    write_profile = Profile(name="w", project="P2", permission="write")
    switcher.switch([write_profile], [write_profile, read_profile], numeric_config, None, fake_client, state)
    assert state.grants[0].user_id == 200  # writer


def test_switch_read_uses_reader_key_in_export_output(
    fake_client: FakeBacklogClient, numeric_config: DefaultConfig, state: State
) -> None:
    profile = Profile(name="r", project="P1", permission="read")

    lines, overall = switcher.switch([profile], [profile], numeric_config, None, fake_client, state)

    assert overall == "read"
    assert f"export BACKLOG_API_KEY={READER_KEY}" in lines


def test_switch_write_uses_writer_key_in_export_output(
    fake_client: FakeBacklogClient, numeric_config: DefaultConfig, state: State
) -> None:
    profile = Profile(name="w", project="P1", permission="write")

    lines, overall = switcher.switch([profile], [profile], numeric_config, None, fake_client, state)

    assert overall == "write"
    assert f"export BACKLOG_API_KEY={WRITER_KEY}" in lines


def test_switch_admin_uses_writer_key_in_export_output(
    fake_client: FakeBacklogClient, numeric_config: DefaultConfig, state: State
) -> None:
    profile = Profile(name="a", project="P1", permission="admin")

    lines, overall = switcher.switch([profile], [profile], numeric_config, None, fake_client, state)

    assert overall == "admin"
    assert f"export BACKLOG_API_KEY={WRITER_KEY}" in lines


# ---------------------------------------------------------------------------
# get_status
# ---------------------------------------------------------------------------


def test_get_status_reflects_actual_membership(
    fake_client: FakeBacklogClient, numeric_config: DefaultConfig, state: State
) -> None:
    profile = Profile(name="w", project="P1", permission="write")
    switcher.switch([profile], [profile], numeric_config, None, fake_client, state)

    text = switcher.get_status([profile], numeric_config, fake_client, state)

    assert "w" in text
    assert "P1" in text
    assert "write" in text


def test_get_status_no_profiles_message(
    fake_client: FakeBacklogClient, numeric_config: DefaultConfig, state: State
) -> None:
    text = switcher.get_status([], numeric_config, fake_client, state)

    assert "プロファイルが定義されていません" in text
