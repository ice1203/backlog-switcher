"""keys.py（APIキー解決）の単体テスト。

subprocess は一切実行しない。すべて mocker.patch でモックする。
"""

from __future__ import annotations

import subprocess

import pytest
from pytest_mock import MockerFixture

from bswitch import keys
from bswitch.models import DefaultConfig


def _config() -> DefaultConfig:
    return DefaultConfig(
        space="test-space.backlog.com",
        writer_user="200",
        reader_user="100",
        writer_api_key_ref="op://MyVault/backlog-svc-writer/credential",
        reader_api_key_ref="op://MyVault/backlog-svc-reader/credential",
    )


def test_writer_env_fallback_takes_priority_over_op(monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture) -> None:
    monkeypatch.setenv(keys.ENV_WRITER_API_KEY, "writer-dummy-key")
    run_mock = mocker.patch("bswitch.keys.subprocess.run")

    result = keys.resolve_writer_key(_config())

    assert result == "writer-dummy-key"
    run_mock.assert_not_called()


def test_reader_env_fallback_takes_priority_over_op(monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture) -> None:
    monkeypatch.setenv(keys.ENV_READER_API_KEY, "reader-dummy-key")
    run_mock = mocker.patch("bswitch.keys.subprocess.run")

    result = keys.resolve_reader_key(_config())

    assert result == "reader-dummy-key"
    run_mock.assert_not_called()


def test_op_read_called_when_env_unset(monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture) -> None:
    monkeypatch.delenv(keys.ENV_WRITER_API_KEY, raising=False)
    run_mock = mocker.patch(
        "bswitch.keys.subprocess.run",
        return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="op-dummy-key\n", stderr=""),
    )

    result = keys.resolve_writer_key(_config())

    assert result == "op-dummy-key"
    run_mock.assert_called_once_with(
        ["op", "read", "op://MyVault/backlog-svc-writer/credential"],
        capture_output=True,
        text=True,
        check=False,
    )


def test_op_read_nonzero_exit_raises_without_leaking_key(
    monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture
) -> None:
    monkeypatch.delenv(keys.ENV_WRITER_API_KEY, raising=False)
    mocker.patch(
        "bswitch.keys.subprocess.run",
        return_value=subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="denied"),
    )

    with pytest.raises(keys.KeyResolutionError) as excinfo:
        keys.resolve_writer_key(_config())

    assert "op-dummy-key" not in str(excinfo.value)


def test_op_read_empty_output_raises(monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture) -> None:
    monkeypatch.delenv(keys.ENV_WRITER_API_KEY, raising=False)
    mocker.patch(
        "bswitch.keys.subprocess.run",
        return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout="   \n", stderr=""),
    )

    with pytest.raises(keys.KeyResolutionError):
        keys.resolve_writer_key(_config())


def test_op_not_found_raises_with_fallback_hint(monkeypatch: pytest.MonkeyPatch, mocker: MockerFixture) -> None:
    monkeypatch.delenv(keys.ENV_WRITER_API_KEY, raising=False)
    mocker.patch("bswitch.keys.subprocess.run", side_effect=FileNotFoundError())

    with pytest.raises(keys.KeyResolutionError) as excinfo:
        keys.resolve_writer_key(_config())

    assert keys.ENV_WRITER_API_KEY in str(excinfo.value)


def test_resolve_api_key_read_uses_reader_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(keys.ENV_READER_API_KEY, "reader-dummy-key")
    monkeypatch.setenv(keys.ENV_WRITER_API_KEY, "writer-dummy-key")

    assert keys.resolve_api_key("read", _config()) == "reader-dummy-key"


@pytest.mark.parametrize("permission", ["write", "admin"])
def test_resolve_api_key_write_and_admin_use_writer_key(monkeypatch: pytest.MonkeyPatch, permission: str) -> None:
    monkeypatch.setenv(keys.ENV_READER_API_KEY, "reader-dummy-key")
    monkeypatch.setenv(keys.ENV_WRITER_API_KEY, "writer-dummy-key")

    assert keys.resolve_api_key(permission, _config()) == "writer-dummy-key"


def test_resolve_api_key_unknown_permission_raises_valueerror() -> None:
    with pytest.raises(ValueError, match="owner"):
        keys.resolve_api_key("owner", _config())
