# Backlog API 仕様

公式ドキュメント: https://developer.nulab.com/ja/docs/backlog/

## 認証

クエリパラメータ `apiKey` で渡す。

```
GET https://<space>/api/v2/users?apiKey=<key>
```

## 使用するAPI一覧

| API | エンドポイント | 必要権限 | 出典 |
|---|---|---|---|
| プロジェクトユーザー追加 | `POST /api/v2/projects/:projectIdOrKey/users`（form: `userId`） | 管理者 または プロジェクト管理者 | https://developer.nulab.com/ja/docs/backlog/api/2/add-project-user/ |
| プロジェクトユーザー削除 | `DELETE /api/v2/projects/:projectIdOrKey/users`（form: `userId`） | 管理者 または プロジェクト管理者 | https://developer.nulab.com/ja/docs/backlog/api/2/delete-project-user/ |
| プロジェクトユーザー一覧取得 | `GET /api/v2/projects/:projectIdOrKey/users` | 管理者 または プロジェクト管理者（status実装用） | https://developer.nulab.com/ja/docs/backlog/api/2/get-project-user-list/ |
| プロジェクト管理者追加 | `POST /api/v2/projects/:projectIdOrKey/administrators`（form: `userId`） | ドキュメント上は「管理者」のみ（注記参照） | https://developer.nulab.com/ja/docs/backlog/api/2/add-project-administrator/ |
| プロジェクト管理者一覧取得 | `GET /api/v2/projects/:projectIdOrKey/administrators` | 管理者 または プロジェクト管理者（status/release実装用） | https://developer.nulab.com/ja/docs/backlog/api/2/get-project-administrator-list/ |
| プロジェクト管理者削除 | `DELETE /api/v2/projects/:projectIdOrKey/administrators`（form: `userId`） | 管理者 または プロジェクト管理者（release実装用） | https://developer.nulab.com/ja/docs/backlog/api/2/delete-project-administrator/ |
| ユーザー一覧取得 | `GET /api/v2/users` | 管理者 または プロジェクト管理者。レスポンスに数値id・name・mailAddressを含む（メール→数値IDの解決に使用） | https://developer.nulab.com/ja/docs/backlog/api/2/get-user-list/ |
| ユーザー情報更新 | `PATCH /api/v2/users/:userId` | 管理者。**新プランのスペースでは利用不可**のため本ツールでは使用しない | https://developer.nulab.com/docs/backlog/api/2/update-user/ |

## エラーレスポンス

```json
{
  "errors": [
    {
      "message": "...",
      "code": <整数>,
      "moreInfo": ""
    }
  ]
}
```

権限エラー: HTTPステータス `403`。`errors[0].code` で詳細を判別できる。

## 注記

### プロジェクト管理者追加API

ドキュメントの実行可能権限は「管理者」のみだが、UI上はプロジェクト管理者でもフラグを付けられるという実体験報告があり、API挙動は未確定。よって `admin` レベルは「試行し、権限エラーなら write に縮退して警告」という実装にする。E2E環境ではマスターがスペース管理者のためこの縮退パスは再現できない（単体テストで担保）。

### ユーザーID解決

`writer_user` / `reader_user` のメールアドレス指定は `GET /api/v2/users` で数値IDに解決し、`state.json` にキャッシュする。2回目以降は解決APIを呼ばない。数値ID直接指定も動く。

### 冪等性

- 既に参加済みユーザーへの追加リクエスト → エラーにならない
- 未参加ユーザーへの削除リクエスト → エラーにならない
