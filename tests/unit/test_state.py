"""state.py（state.json 読み書き）の単体テスト。"""

from __future__ import annotations

import json
import stat
from pathlib import Path

from bswitch.models import Grant, State
from bswitch.state import load_state, save_state


def test_load_state_missing_file_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "state.json"

    state = load_state(path)

    assert state.grants == []
    assert state.user_id_cache == {}


def test_load_state_empty_file_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("", encoding="utf-8")

    state = load_state(path)

    assert state.grants == []
    assert state.user_id_cache == {}


def test_load_state_corrupted_json_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not valid json", encoding="utf-8")

    state = load_state(path)

    assert state.grants == []
    assert state.user_id_cache == {}


def test_save_then_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    original = State(
        grants=[
            Grant(profile="customer-a", project="CUSTOMER_A", user_id=100, permission="read", expires_at=None),
            Grant(
                profile="customer-b",
                project="CUSTOMER_B",
                user_id=200,
                permission="write",
                expires_at="2026-07-16T00:00:00+00:00",
            ),
        ],
        user_id_cache={"svc-writer@example.com": 200, "svc-reader@example.com": 100},
    )

    save_state(path, original)
    reloaded = load_state(path)

    assert reloaded.to_dict() == original.to_dict()


def test_state_json_has_no_api_key_field(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    original = State(
        grants=[Grant(profile="p", project="PROJ", user_id=1, permission="read", expires_at=None)],
        user_id_cache={"a@example.com": 1},
    )

    save_state(path, original)
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)

    assert set(data.keys()) == {"grants", "user_id_cache"}
    for grant in data["grants"]:
        assert set(grant.keys()) == {"profile", "project", "user_id", "permission", "expires_at"}

    for forbidden in ("api_key", "apiKey", "credential", "op://"):
        assert forbidden not in raw


def test_save_state_sets_restrictive_permissions(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    save_state(path, State())

    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


def test_save_state_creates_parent_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "dir" / "state.json"
    save_state(path, State())

    assert path.is_file()


def test_save_state_no_leftover_tmp_file(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    save_state(path, State())

    tmp_path_candidates = list(tmp_path.glob("*.tmp"))
    assert tmp_path_candidates == []
