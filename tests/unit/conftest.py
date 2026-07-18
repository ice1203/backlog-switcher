"""tests/unit 配下で共有するフィクスチャ。

このディレクトリのテストは実API・実サブプロセス（httpx / subprocess）を一切呼ばない。
Backlog API呼び出しは本ファイルで定義する `FakeBacklogClient` に置き換える。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from bswitch.models import DefaultConfig, State


@dataclass
class FakeBacklogClient:
    """switcher.py が期待するBacklogClientインターフェースを模したフェイク実装。

    実HTTP通信は一切行わない。呼び出し順序を `call_log` に記録するため、
    switch()の排他動作（除名→参加の順序）をモックで検証できる。
    """

    call_log: list[tuple[Any, ...]] = field(default_factory=list)
    members: dict[str, set[int]] = field(default_factory=dict)
    admins: dict[str, set[int]] = field(default_factory=dict)
    users: list[dict[str, Any]] = field(default_factory=list)
    #: このセットに含まれるプロジェクトへの add_project_administrator は
    #: 「権限エラー(code 5)によりNoneが返る」を模擬する（admin→write縮退のテスト用）。
    admin_denied_projects: set[str] = field(default_factory=set)
    get_users_calls: int = 0

    def get_project_users(self, project: str) -> list[dict[str, Any]]:
        self.call_log.append(("get_project_users", project))
        return [{"id": uid} for uid in sorted(self.members.get(project, set()))]

    def add_project_user(self, project: str, user_id: int) -> dict[str, Any]:
        self.call_log.append(("add_project_user", project, user_id))
        self.members.setdefault(project, set()).add(user_id)
        return {"id": user_id}

    def delete_project_user(self, project: str, user_id: int) -> dict[str, Any] | None:
        self.call_log.append(("delete_project_user", project, user_id))
        self.members.get(project, set()).discard(user_id)
        return None

    def get_project_administrators(self, project: str) -> list[dict[str, Any]]:
        self.call_log.append(("get_project_administrators", project))
        return [{"id": uid} for uid in sorted(self.admins.get(project, set()))]

    def add_project_administrator(self, project: str, user_id: int) -> dict[str, Any] | None:
        self.call_log.append(("add_project_administrator", project, user_id))
        if project in self.admin_denied_projects:
            # code 5 (UnauthorizedOperationError) 相当: Noneを返す（例外は投げない）。
            return None
        self.admins.setdefault(project, set()).add(user_id)
        return {"id": user_id}

    def delete_project_administrator(self, project: str, user_id: int) -> dict[str, Any] | None:
        self.call_log.append(("delete_project_administrator", project, user_id))
        self.admins.get(project, set()).discard(user_id)
        return None

    def get_users(self) -> list[dict[str, Any]]:
        self.call_log.append(("get_users",))
        self.get_users_calls += 1
        return self.users

    # BacklogClientと同様にコンテキストマネージャとして使えるようにする（cli.py経由のテスト用）。
    def __enter__(self) -> FakeBacklogClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


@pytest.fixture
def fake_client() -> FakeBacklogClient:
    """呼び出し順序・参加状態を記録するフェイクBacklogClient。実HTTP通信は一切行わない。"""
    return FakeBacklogClient()


@pytest.fixture
def numeric_config() -> DefaultConfig:
    """writer/readerを数値IDで直接指定したDefaultConfig（メール解決APIを呼ばせないため）。"""
    return DefaultConfig(
        space="test-space.backlog.com",
        writer_user="200",
        reader_user="100",
        writer_api_key_ref="op://MyVault/backlog-svc-writer/credential",
        reader_api_key_ref="op://MyVault/backlog-svc-reader/credential",
        default_duration=None,
    )


@pytest.fixture
def state() -> State:
    return State()
