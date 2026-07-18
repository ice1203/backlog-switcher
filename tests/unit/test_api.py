"""api.py（BacklogClient）の単体テスト。

httpx.MockTransport で実HTTP通信を完全に置き換える。実APIには一切接続しない。
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from bswitch.api import BacklogApiError, BacklogClient
from bswitch.api import PermissionError as ApiPermissionError

#: テスト専用のダミーAPIキー（実キーではない）。
API_KEY = "unit-test-dummy-master-key"


def _client_with_handler(
    handler: Callable[[httpx.Request], httpx.Response], *, api_key: str = API_KEY
) -> BacklogClient:
    client = BacklogClient("example.backlog.com", api_key)
    # 実HTTP通信を発生させないよう、内部の httpx.Client を MockTransport 版に差し替える。
    client._client = httpx.Client(  # noqa: SLF001 - テスト用の意図的な差し替え
        transport=httpx.MockTransport(handler),
        base_url="https://example.backlog.com",
    )
    return client


def test_all_requests_include_apikey_query_param() -> None:
    seen_params: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_params.append(dict(request.url.params))
        if request.url.path == "/api/v2/users":
            return httpx.Response(200, json=[])
        if request.url.path.endswith("/administrators"):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json=[] if request.method == "GET" else {"id": 1})

    client = _client_with_handler(handler)
    client.add_project_user("PROJ", 1)
    client.delete_project_user("PROJ", 1)
    client.get_project_users("PROJ")
    client.add_project_administrator("PROJ", 1)
    client.delete_project_administrator("PROJ", 1)
    client.get_project_administrators("PROJ")
    client.get_users()

    assert len(seen_params) == 7
    assert all(params.get("apiKey") == API_KEY for params in seen_params)


def test_add_project_user_success() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["path"] = request.url.path
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, json={"id": 42})

    client = _client_with_handler(handler)
    result = client.add_project_user("PROJ", 42)

    assert captured["method"] == "POST"
    assert captured["path"] == "/api/v2/projects/PROJ/users"
    assert captured["params"]["apiKey"] == API_KEY  # type: ignore[index]
    assert result == {"id": 42}


def test_delete_project_user_success_returns_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        return httpx.Response(200, json={"id": 42})

    client = _client_with_handler(handler)
    result = client.delete_project_user("PROJ", 42)

    assert result == {"id": 42}


def test_delete_project_user_ignores_error_for_idempotency() -> None:
    """未参加ユーザーの除名（code 6: NoResourceError）はエラーを無視してNoneを返す（冪等性）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"errors": [{"message": "ユーザーが見つかりません", "code": 6}]})

    client = _client_with_handler(handler)
    result = client.delete_project_user("PROJ", 999)

    assert result is None


def test_delete_project_user_rate_limit_raises_backlogapierror() -> None:
    """レートリミット（code 13）は無視せず例外を伝播する（除名に見えて実は失敗している事故の防止）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"errors": [{"message": "リクエストが多すぎます", "code": 13}]})

    client = _client_with_handler(handler)
    with pytest.raises(BacklogApiError) as excinfo:
        client.delete_project_user("PROJ", 1)

    assert excinfo.value.code == 13


def test_delete_project_user_auth_error_raises_backlogapierror() -> None:
    """認証エラー（code 11）は無視せず例外を伝播する。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"errors": [{"message": "認証に失敗しました", "code": 11}]})

    client = _client_with_handler(handler)
    with pytest.raises(BacklogApiError) as excinfo:
        client.delete_project_user("PROJ", 1)

    assert excinfo.value.code == 11


def test_delete_project_user_permission_error_returns_none() -> None:
    """権限エラー（code 5）は排他除名スイープを止めないよう無視してNoneを返す。

    マスターユーザーに除名権限のないプロジェクトでスイープが止まると
    switch自体が一切できなくなるため、スキップして継続する。
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"errors": [{"message": "権限がありません", "code": 5}]})

    client = _client_with_handler(handler)
    assert client.delete_project_user("PROJ", 1) is None


def test_delete_project_user_unparseable_body_raises_backlogapierror() -> None:
    """レスポンスボディがパースできない場合（ネットワークエラー等相当）も例外を伝播する。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal server error")

    client = _client_with_handler(handler)
    with pytest.raises(BacklogApiError):
        client.delete_project_user("PROJ", 1)


def test_get_project_users_returns_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(200, json=[{"id": 100, "name": "reader"}])

    client = _client_with_handler(handler)
    result = client.get_project_users("PROJ")

    assert result == [{"id": 100, "name": "reader"}]


def test_add_project_administrator_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/projects/PROJ/administrators"
        return httpx.Response(200, json={"id": 200})

    client = _client_with_handler(handler)
    result = client.add_project_administrator("PROJ", 200)

    assert result == {"id": 200}


def test_add_project_administrator_code5_returns_none() -> None:
    """errors[].code == 5（UnauthorizedOperationError）はNoneを返す（admin→write縮退用）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"errors": [{"message": "権限がありません", "code": 5}]})

    client = _client_with_handler(handler)
    result = client.add_project_administrator("PROJ", 200)

    assert result is None


def test_add_project_administrator_other_error_raises_backlogapierror() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"errors": [{"message": "プロジェクトが存在しません", "code": 6}]})

    client = _client_with_handler(handler)
    with pytest.raises(BacklogApiError) as excinfo:
        client.add_project_administrator("PROJ", 200)

    assert excinfo.value.code == 6
    assert API_KEY not in str(excinfo.value)


def test_delete_project_administrator_ignores_error_for_idempotency() -> None:
    """対象が元々管理者でない場合（code 6: NoResourceError）はエラーを無視してNoneを返す（冪等性）。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"errors": [{"message": "管理者ではありません", "code": 6}]})

    client = _client_with_handler(handler)
    result = client.delete_project_administrator("PROJ", 999)

    assert result is None


def test_delete_project_administrator_rate_limit_raises_backlogapierror() -> None:
    """レートリミット（code 13）は無視せず例外を伝播する。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"errors": [{"message": "リクエストが多すぎます", "code": 13}]})

    client = _client_with_handler(handler)
    with pytest.raises(BacklogApiError) as excinfo:
        client.delete_project_administrator("PROJ", 1)

    assert excinfo.value.code == 13


def test_delete_project_administrator_permission_error_returns_none() -> None:
    """権限エラー（code 5）は排他除名スイープを止めないよう無視してNoneを返す。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"errors": [{"message": "権限がありません", "code": 5}]})

    client = _client_with_handler(handler)
    assert client.delete_project_administrator("PROJ", 1) is None


def test_delete_project_administrator_unparseable_body_raises_backlogapierror() -> None:
    """レスポンスボディがパースできない場合も例外を伝播する。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal server error")

    client = _client_with_handler(handler)
    with pytest.raises(BacklogApiError):
        client.delete_project_administrator("PROJ", 1)


def test_get_project_administrators_returns_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": 200, "name": "writer"}])

    client = _client_with_handler(handler)
    result = client.get_project_administrators("PROJ")

    assert result == [{"id": 200, "name": "writer"}]


def test_get_users_returns_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/users"
        return httpx.Response(
            200,
            json=[
                {"id": 200, "name": "writer", "mailAddress": "writer@example.com"},
                {"id": 100, "name": "reader", "mailAddress": "reader@example.com"},
            ],
        )

    client = _client_with_handler(handler)
    result = client.get_users()

    assert result == [
        {"id": 200, "name": "writer", "mailAddress": "writer@example.com"},
        {"id": 100, "name": "reader", "mailAddress": "reader@example.com"},
    ]


def test_add_project_user_permission_error_raises_permission_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"errors": [{"message": "権限がありません", "code": 5}]})

    client = _client_with_handler(handler)
    with pytest.raises(ApiPermissionError):
        client.add_project_user("PROJ", 1)


def test_error_message_never_contains_api_key() -> None:
    """例外メッセージ・__repr__のいずれにもAPIキーの値が含まれないこと。"""
    secret_marker = "super-secret-marker-value-zzz"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal server error")

    client = _client_with_handler(handler, api_key=secret_marker)

    with pytest.raises(BacklogApiError) as excinfo:
        client.get_users()

    assert secret_marker not in str(excinfo.value)
    assert secret_marker not in repr(client)
    assert repr(client) == "BacklogClient(...)"
