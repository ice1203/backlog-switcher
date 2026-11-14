# Backlog API 仕様

公式ドキュメント: https://developer.nulab.com/ja/docs/backlog/

## 認証

クエリパラメータ `apiKey` で渡す。

```
GET https://<space>/api/v2/users?apiKey=<key>
```

## OAuth 2.0

出典: https://developer.nulab.com/ja/docs/backlog/auth/

- フローは認可コードグラント (Authorization Code Grant) のみ。PKCE の記載はない
- 認可エンドポイント: `GET https://<space>/OAuth2AccessRequest.action` (`response_type=code`, `client_id`, `redirect_uri` が必須, `state` は任意)
- トークンエンドポイント: `POST https://<space>/api/v2/oauth2/token` (`application/x-www-form-urlencoded`)
  - 認可コードの交換 (`grant_type=authorization_code`) と更新 (`grant_type=refresh_token`) のどちらも `client_secret` が必須
- API 呼び出し: `Authorization: Bearer <access_token>`
- アクセストークンの有効期間は 3600 秒。リフレッシュトークンの有効期間, 更新時のローテーション, 失効手段は記載がない
- スコープはない。トークンは認可したユーザーの権限をすべて持つ
- `redirect_uri` は開発アプリに登録した値と一致させる。`http://localhost:<port>/callback` を登録して使えることを実機で確認した
- 開発アプリは https://backlog.com/developer/applications/ で登録する。backlog.jp のスペースでも使える
- 認可画面 (`OAuth2AccessRequest.action`) は, 未登録の `client_id` を渡すと 404 を返す (実機)。別アプリの `client_id` (登録された Redirect URI が異なる) を渡したときも 404 だった

### Backlog MCP サーバーの OAuth モード

出典: https://github.com/nulab/backlog-mcp-server (v0.12.0 以降, v0.23.3 で確認)

- stdio モードは API キー (`BACKLOG_API_KEY`) のみに対応する。アクセストークンを環境変数で受け取る手段はない
- OAuth は HTTP モード (`MCP_TRANSPORT=http`) で使う。MCP サーバー自身が Backlog の OAuth クライアントになり, ブラウザで認可したユーザーとして Backlog を操作する。トークンの更新も MCP サーバーが行う
  - 必要な環境変数: `BACKLOG_DOMAIN`, `BACKLOG_OAUTH_CLIENT_ID`, `BACKLOG_OAUTH_CLIENT_SECRET`, `MCP_SERVER_BASE_URL`
  - 開発アプリの Redirect URI は `<MCP_SERVER_BASE_URL>/callback`
  - `0.0.0.0` で待ち受ける場合 (Docker など) は, 受け付ける Host ヘッダーのホスト名 (`MCP_HTTP_ALLOWED_HOSTS` / `--http-allowed-hosts`, ポート番号は見ない) の指定が実質必須。指定しないと DNS リバインディング対策が効かず, 起動時に警告が出る
- Claude Code とは MCP 認可仕様 (動的クライアント登録) で接続する。Claude Code に渡るのは MCP サーバー用のトークンで, Backlog のトークンは MCP サーバーの中にとどまる
- クライアント登録とトークンはメモリにのみ保持する。MCP サーバーを再起動すると, Claude Code の `/mcp` で認証情報を消去してから認証し直す必要がある (消去しないと `invalid_client` / `Unknown client_id` になる)
- 環境変数名 `BACKLOG_OAUTH_CLIENT_ID` / `BACKLOG_OAUTH_CLIENT_SECRET` は Backlog CLI の bee と共通のため, MCP サーバーの起動時にシェルに bee 用の値があると, MCP サーバーに渡る値と混ざるおそれがある

### 認可するユーザーの指定

Claude Code は MCP の認可画面を既定のブラウザで開くため, 既定のブラウザにログインしているユーザーで認可される。Claude Code の起動時に環境変数 `BROWSER` に URL を開くコマンドを指定すると, そのコマンドで認可画面が開く (公式ドキュメントに記載はなく, 実機で確認した)。仮想ユーザー用の Chrome プロファイルを開くコマンドを指定すれば, 仮想ユーザーとして認可できる。

### 接続できるシェルの限定

出典: https://code.claude.com/docs/en/mcp (Environment variable expansion in `.mcp.json`)

- Claude Code は MCP サーバーの設定の `url` / `headers` などに書いた `${VAR}` を, 起動したシェルの環境変数で置き換える。変数が未設定で既定値もない場合は `${VAR}` の文字のまま使う
- ドキュメントの記載は `.mcp.json` についてのみだが, ユーザースコープ (`~/.claude.json`) の登録でも置き換わることを実機で確認した。変数が未設定のシェルで起動すると `'url' is not a valid URL` で接続に失敗し, 設定したシェルではつながる

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
