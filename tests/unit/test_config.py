"""config.py の単体テスト（INIパース・必須項目バリデーション・パス解決）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from bswitch.config import DEFAULT_CONFIG_PATH, ConfigError, get_config_path, load_config

VALID_INI = """\
[default]
space = test-space.backlog.com
writer_user = svc-writer@example.com
reader_user = svc-reader@example.com
writer_api_key_ref = op://MyVault/backlog-svc-writer/credential
reader_api_key_ref = op://MyVault/backlog-svc-reader/credential
default_duration = 8h

[profile test-read]
project = MY_PROJECT
permission = read

[profile test-write]
project = MY_PROJECT
permission = write
"""


def _write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "config"
    path.write_text(content, encoding="utf-8")
    return path


def test_load_valid_config(tmp_path: Path) -> None:
    path = _write(tmp_path, VALID_INI)
    config = load_config(path)

    assert config.default.space == "test-space.backlog.com"
    assert config.default.writer_user == "svc-writer@example.com"
    assert config.default.reader_user == "svc-reader@example.com"
    assert config.default.default_duration == "8h"
    assert set(config.profiles) == {"test-read", "test-write"}
    assert config.profiles["test-read"].project == "MY_PROJECT"
    assert config.profiles["test-read"].permission == "read"
    assert config.profiles["test-write"].permission == "write"
    assert config.all_projects() == ["MY_PROJECT"]
    assert config.state_path == path.parent / "state.json"


def test_default_duration_is_optional(tmp_path: Path) -> None:
    content = VALID_INI.replace("default_duration = 8h\n", "")
    path = _write(tmp_path, content)

    config = load_config(path)

    assert config.default.default_duration is None


@pytest.mark.parametrize(
    "missing_key",
    ["space", "writer_user", "reader_user", "writer_api_key_ref", "reader_api_key_ref"],
)
def test_missing_required_default_field_raises_configerror(tmp_path: Path, missing_key: str) -> None:
    lines = [line for line in VALID_INI.splitlines() if not line.startswith(f"{missing_key} ")]
    path = _write(tmp_path, "\n".join(lines))

    with pytest.raises(ConfigError):
        load_config(path)


def test_missing_project_in_profile_raises_configerror(tmp_path: Path) -> None:
    content = VALID_INI.replace("project = MY_PROJECT\npermission = read", "permission = read")
    path = _write(tmp_path, content)

    with pytest.raises(ConfigError):
        load_config(path)


def test_missing_permission_in_profile_raises_configerror(tmp_path: Path) -> None:
    content = VALID_INI.replace("project = MY_PROJECT\npermission = read", "project = MY_PROJECT")
    path = _write(tmp_path, content)

    with pytest.raises(ConfigError):
        load_config(path)


def test_invalid_permission_value_raises_configerror(tmp_path: Path) -> None:
    content = VALID_INI.replace("permission = read", "permission = superadmin")
    path = _write(tmp_path, content)

    with pytest.raises(ConfigError):
        load_config(path)


def test_duplicate_profile_name_raises_configerror(tmp_path: Path) -> None:
    # configparser自体は同一のセクション名文字列の重複しか検出しないため、
    # 正規化後（前後空白除去後）に衝突する別表記のセクション名で検証する。
    content = VALID_INI + "\n[profile  test-read]\nproject = OTHER\npermission = write\n"
    path = _write(tmp_path, content)

    with pytest.raises(ConfigError):
        load_config(path)


def test_empty_profile_name_raises_configerror(tmp_path: Path) -> None:
    content = VALID_INI + "\n[profile ]\nproject = OTHER\npermission = write\n"
    path = _write(tmp_path, content)

    with pytest.raises(ConfigError):
        load_config(path)


def test_config_not_found_exits_with_sample(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing_path = tmp_path / "does-not-exist"

    with pytest.raises(SystemExit) as excinfo:
        load_config(missing_path)

    assert excinfo.value.code == 1
    captured = capsys.readouterr()
    assert "設定ファイルが見つかりません" in captured.err
    assert "[default]" in captured.err
    assert "writer_api_key_ref" in captured.err


def test_bswitch_config_env_overrides_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write(tmp_path, VALID_INI)
    monkeypatch.setenv("BSWITCH_CONFIG", str(path))

    assert get_config_path() == path
    config = load_config()
    assert config.default.space == "test-space.backlog.com"


def test_get_config_path_defaults_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BSWITCH_CONFIG", raising=False)

    assert get_config_path() == DEFAULT_CONFIG_PATH


def test_get_profile_unknown_raises_configerror(tmp_path: Path) -> None:
    path = _write(tmp_path, VALID_INI)
    config = load_config(path)

    with pytest.raises(ConfigError):
        config.get_profile("nope")
