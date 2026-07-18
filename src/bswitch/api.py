"""Backlog REST API v2 クライアント。

APIキーは全リクエストにクエリパラメータ `apiKey` として付与する
（出典: https://developer.nulab.com/ja/docs/backlog/auth/ ）。

呼び出すエンドポイントは docs/design.md「禁止事項」で許可された範囲のみ:
  - プロジェクトユーザー追加/削除/一覧
  - プロジェクト管理者追加/削除/一覧
  - ユーザー一覧（メール→ID解決用）
課題・Wiki・プロジェクト自体を変更/削除するAPIは呼ばない。

APIキーはいかなる例外メッセージにも含めない。
"""

from __future__ import annotations

from typing import Any

import httpx

#: errors[].code == 5: UnauthorizedOperationError
CODE_UNAUTHORIZED_OPERATION = 5

#: errors[].code == 6: NoResourceError（対象リソース不在）。
#: delete系メソッドで「対象ユーザーが元々参加/管理者でない」ことを示す唯一の無視してよいエラー。
CODE_NO_RESOURCE = 6


class BacklogApiError(Exception):
    """Backlog API呼び出し失敗時の例外。APIキーは含めない。"""

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        project: str | None = None,
        operation: str | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.project = project
        self.operation = operation
        if project and operation:
            display = f"{project}への{operation}に失敗: {message}"
        elif operation:
            display = f"{operation}に失敗: {message}"
        else:
            display = message
        super().__init__(display)


class PermissionError(BacklogApiError):  # noqa: A001 - 設計書で指定されたクラス名
    """権限不足エラー（errors[].code == 5: UnauthorizedOperationError）。

    主にプロジェクト管理者追加APIの admin→write 縮退判定に使われる。
    """


class BacklogClient:
    """Backlog REST API v2 クライアント（httpx使用）。"""

    def __init__(self, space: str, api_key: str, *, timeout: float = 10.0) -> None:
        self._api_key = api_key
        self._client = httpx.Client(base_url=f"https://{space}", timeout=timeout)

    def __repr__(self) -> str:
        # APIキーを絶対に文字列化・ログに残さない
        return "BacklogClient(...)"

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> BacklogClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- 内部ヘルパ ---------------------------------------------------

    def _params(self, **kwargs: object) -> dict[str, object]:
        return {"apiKey": self._api_key, **kwargs}

    @staticmethod
    def _extract_errors(response: httpx.Response) -> list[dict[str, Any]]:
        try:
            body = response.json()
        except ValueError:
            return []
        if isinstance(body, dict) and isinstance(body.get("errors"), list):
            return [e for e in body["errors"] if isinstance(e, dict)]
        return []

    @staticmethod
    def _safe_json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return None

    def _raise_for_error(self, response: httpx.Response, *, project: str | None, operation: str) -> None:
        """エラーレスポンスから例外を送出する。呼び出し前に status_code >= 400 を確認すること。"""
        errors = self._extract_errors(response)
        if errors:
            first = errors[0]
            code = first.get("code")
            code_int = code if isinstance(code, int) else None
            message = str(first.get("message") or "不明なエラー")
            if code_int == CODE_UNAUTHORIZED_OPERATION:
                raise PermissionError(message, code=code_int, project=project, operation=operation)
            raise BacklogApiError(message, code=code_int, project=project, operation=operation)
        raise BacklogApiError(
            f"HTTPステータス {response.status_code}",
            code=None,
            project=project,
            operation=operation,
        )

    # -- プロジェクトユーザー -------------------------------------------

    def add_project_user(self, project: str, user_id: int) -> dict[str, Any]:
        """POST /api/v2/projects/:projectIdOrKey/users （form: userId）。

        ユーザーが明示的に選択したプロジェクトへの追加失敗は隠さない。
        権限エラー（code 5）も PermissionError として送出する
        （マスターユーザーがそのプロジェクトの管理者でない＝そのprofileは使えない）。
        """
        response = self._client.post(
            f"/api/v2/projects/{project}/users",
            params=self._params(),
            data={"userId": user_id},
        )
        if response.status_code >= 400:
            self._raise_for_error(response, project=project, operation="ユーザー追加")
        return response.json()

    def delete_project_user(self, project: str, user_id: int) -> dict[str, Any] | None:
        """DELETE /api/v2/projects/:projectIdOrKey/users （form: userId）。

        未参加ユーザーの除名（errors[].code == 6: NoResourceError）のみ、冪等性のため
        None を返し無視する。それ以外のエラー（レートリミット code 13、認証エラー code 11、
        権限エラー code 5 等の一時的/致命的なエラー）は BacklogApiError を送出して呼び出し元に
        伝播させる。レスポンスボディがパースできない場合（ネットワークエラー等）も同様に伝播する。
        """
        response = self._client.request(
            "DELETE",
            f"/api/v2/projects/{project}/users",
            params=self._params(),
            data={"userId": user_id},
        )
        if response.status_code >= 400:
            errors = self._extract_errors(response)
            if errors and errors[0].get("code") in (CODE_NO_RESOURCE, CODE_UNAUTHORIZED_OPERATION):
                return None
            self._raise_for_error(response, project=project, operation="ユーザー除名")
        return self._safe_json(response)

    def get_project_users(self, project: str) -> list[dict[str, Any]]:
        """GET /api/v2/projects/:projectIdOrKey/users。"""
        response = self._client.get(f"/api/v2/projects/{project}/users", params=self._params())
        if response.status_code >= 400:
            self._raise_for_error(response, project=project, operation="ユーザー一覧取得")
        data = response.json()
        return data if isinstance(data, list) else []

    # -- プロジェクト管理者 ---------------------------------------------

    def add_project_administrator(self, project: str, user_id: int) -> dict[str, Any] | None:
        """POST /api/v2/projects/:projectIdOrKey/administrators （form: userId）。

        errors[].code == 5（UnauthorizedOperationError）の場合は例外を投げず None を返す
        （admin→write 縮退用）。それ以外のエラーは BacklogApiError を送出する。
        """
        response = self._client.post(
            f"/api/v2/projects/{project}/administrators",
            params=self._params(),
            data={"userId": user_id},
        )
        if response.status_code >= 400:
            errors = self._extract_errors(response)
            if errors and errors[0].get("code") == CODE_UNAUTHORIZED_OPERATION:
                return None
            self._raise_for_error(response, project=project, operation="プロジェクト管理者追加")
        return response.json()

    def delete_project_administrator(self, project: str, user_id: int) -> dict[str, Any] | None:
        """DELETE /api/v2/projects/:projectIdOrKey/administrators （form: userId）。

        対象ユーザーが元々プロジェクト管理者でない場合（errors[].code == 6: NoResourceError）
        のみ、冪等性のため None を返し無視する。それ以外のエラー（レートリミット code 13、
        認証エラー code 11、権限エラー code 5 等の一時的/致命的なエラー）は BacklogApiError を
        送出して呼び出し元に伝播させる。レスポンスボディがパースできない場合（ネットワークエラー等）
        も同様に伝播する。
        """
        response = self._client.request(
            "DELETE",
            f"/api/v2/projects/{project}/administrators",
            params=self._params(),
            data={"userId": user_id},
        )
        if response.status_code >= 400:
            errors = self._extract_errors(response)
            if errors and errors[0].get("code") in (CODE_NO_RESOURCE, CODE_UNAUTHORIZED_OPERATION):
                return None
            self._raise_for_error(response, project=project, operation="プロジェクト管理者除名")
        return self._safe_json(response)

    def get_project_administrators(self, project: str) -> list[dict[str, Any]]:
        """GET /api/v2/projects/:projectIdOrKey/administrators。"""
        response = self._client.get(f"/api/v2/projects/{project}/administrators", params=self._params())
        if response.status_code >= 400:
            self._raise_for_error(response, project=project, operation="プロジェクト管理者一覧取得")
        data = response.json()
        return data if isinstance(data, list) else []

    # -- ユーザー ------------------------------------------------------

    def get_users(self) -> list[dict[str, Any]]:
        """GET /api/v2/users。メールアドレス→数値ID解決に使用する。"""
        response = self._client.get("/api/v2/users", params=self._params())
        if response.status_code >= 400:
            self._raise_for_error(response, project=None, operation="ユーザー一覧取得")
        data = response.json()
        return data if isinstance(data, list) else []
