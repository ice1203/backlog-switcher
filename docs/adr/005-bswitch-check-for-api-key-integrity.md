# ADR-005: APIキー整合性チェックを専用の軽量サブコマンド `bswitch check` として分離する

## ステータス
承諾済み（一部置き換え済み: `bswitch check` における `op read` 利用は ADR-006 で廃止）

## 日付
2026-07-21

## コンテキスト

bswitch の付与状態は `state.json` に記録され、`bswitch switch` 実行のたびに更新される。
一方、`BACKLOG_API_KEY` 環境変数は `bswitch switch` が stdout に出力した `export` 行をシェル関数が `eval` することで設定される。

この2つが乖離するケースが実運用で発生する:

- Claude Code のセッション内で `bswitch switch` を実行しても、子プロセスが設定した env var は親プロセス（Claude Code のシェル）に伝播しない
- 別ターミナルで異なる権限に切り替えた後に Claude Code セッションを起動すると、`state.json` の付与状態と `BACKLOG_API_KEY` が食い違う

この不整合を Claude Code のステータスライン（`~/.claude/statusline.js`）で視覚的に検出したいというニーズが生まれた。
statusline.js は頻繁に実行されるため、整合チェックに使う手段は軽量でなければならない。

### 検討した選択肢

1. **statusline.js 内で state.json と BACKLOG_READER/WRITER_API_KEY を直接比較**
   - 概要: statusline.js が state.json を読んで permission を判断し、`BACKLOG_READER_API_KEY` / `BACKLOG_WRITER_API_KEY` と `BACKLOG_API_KEY` をインプロセスで比較する
   - メリット: サブプロセス起動なし・純粋なファイル読み取り + env var 比較で最速
   - デメリット: `BACKLOG_READER_API_KEY` / `BACKLOG_WRITER_API_KEY` は任意設定項目（keys.py の解決順序 1 番目のフォールバック）であり、1Password 構成では設定しないユーザーには動作しない。全員に適用できる設計ではない

2. **statusline.js から直接 `op read` を呼ぶ**
   - 概要: statusline.js 内で `exec('op read ...')` を呼んでキーを取得し比較する
   - メリット: 全ユーザーで動作（env var フォールバックがなくても 1Password から取得できる）
   - デメリット: ①ステータスライン更新のたびに 1Password CLI プロセスを起動するため遅い ②1Password ロック時に失敗しステータスラインが不安定になる ③表示レイヤー（statusline.js）が 1Password 参照先という bswitch の内部実装詳細に直接依存するレイヤー違反

3. **`bswitch status` にキー照合カラムを追加**
   - 概要: 既存の `bswitch status` コマンドに KEY 列（`OK` / `MISMATCH`）を追加し、整合チェックを統合する
   - メリット: 既存コマンドに統合できる・ユーザーが別コマンドを覚えなくてよい
   - デメリット: `bswitch status` は Backlog API をプロジェクト数分呼ぶ重量コマンドであり、ステータスライン用途（頻繁な実行）には不向き。また `op read` 失敗がステータス表示全体を壊すリスクがある

4. **新規 `bswitch check` 軽量サブコマンドを追加**
   - 概要: Backlog API を一切呼ばず、state.json 読み取り + `resolve_api_key()`（keys.py の既存実装を流用）で整合チェックを行い、JSON を stdout に出力する専用コマンドを追加する
   - メリット: ①Backlog API 呼び出しなし・`op read` は最大 1 回で軽量 ②keys.py の解決順序（env var → 1Password）を流用するため全ユーザーで動作 ③責務が明確（整合チェック専用コマンド）④JSON 出力で statusline.js がパース容易
   - デメリット: 新サブコマンドの追加コスト（ただし statusline.js 用途のため直接呼ぶ機会は少ない）

## 決定

私たちは **新規 `bswitch check` 軽量サブコマンドを追加し、APIキー整合性チェックの責務を分離する** 方法を使用します。

### 決定理由

- ステータスラインは頻繁に実行されるため、Backlog API 複数回呼び出しは選択肢から除外される（選択肢 3）
- keys.py の `resolve_api_key()` はすでに env var → 1Password の解決順序を実装しており、これを statusline.js 側に再実装するのはレイヤー違反かつ保守コスト増（選択肢 1・2）
- `bswitch check` は Backlog API を呼ばないため、`BSWITCH_MASTER_API_KEY` も不要。これにより `shell-init` / `list` と同様に軽量コマンドとして分類できる
- JSON 出力は statusline.js（Node.js）での parse が容易であり、将来フィールド追加時も後方互換を保ちやすい

### 副次的な決定事項

- **`BSWITCH_MASTER_API_KEY` チェックのバイパス**: `bswitch check` は Backlog API を呼ばないため、`main()` の master key チェックより前に処理を完結させる。同時に `bswitch list` も master key を実際には使わないため、同様に master key チェック前に移動する（`load_config()` を master key チェックより前に移動する構成変更を伴う）
- **エラー時の動作**: `op read` 失敗などの異常系は exit 1 + stdout 空とし、statusline.js 側は null チェックで安全にスキップする
- **出力形式**: JSON 配列。グラントなし時は `[]`

```json
[
  {
    "profile": "customer-a-read",
    "permission": "read",
    "status": "OK"
  }
]
```

`status` の値: `OK`（一致）/ `MISMATCH`（不一致）/ `NOT_SET`（`BACKLOG_API_KEY` 未設定）

## 結果

### ポジティブ
- Backlog API 呼び出しなし・`op read` 最大 1 回という軽量実行でステータスライン用途に適する
- `BACKLOG_READER_API_KEY` / `BACKLOG_WRITER_API_KEY` が未設定の 1Password 構成でも全ユーザーが使用できる
- 整合チェックのロジックが bswitch 内部（keys.py の解決順序）に集約され、表示レイヤー（statusline.js）がその実装詳細を知る必要がなくなる
- `BSWITCH_MASTER_API_KEY` 不要により、bswitch が起動していない環境やマスターキーが未設定の状態でも `bswitch check` が動作する

### ネガティブ
- `bswitch check` という新サブコマンドが増え、コードベースの表面積が広がる
- `op read` はステータスライン更新のたびに呼ばれる可能性があり、1Password ロック中は整合チェックが失敗する（ただし失敗時は静かにスキップするため UX への影響は限定的）

### トレードオフ
- `op read` の実行コスト（数百 ms）を毎回払うことを選択肢 1（最速）と比べて受け入れる。その代わりに全ユーザーへの適用可能性と責務分離を得る
- Backlog API による実際の参加状況確認は行わない。`state.json` の内容が正確であることを前提とした「ベストエフォートな整合チェック」である（実際の参加状況確認が必要なら引き続き `bswitch status` を使う）

## コンプライアンス
- `bswitch check` の実装は Backlog API (`BacklogClient`) を使用してはならない
- `bswitch check` は `BSWITCH_MASTER_API_KEY` を要求してはならない
- `bswitch check` の stdout には JSON 配列のみを出力すること（エラーメッセージは stderr へ）
- `op read` 失敗時は stdout を空にして exit 1 で終了すること（statusline.js が null チェックで対応する）
- `main()` の `load_config()` 呼び出しを master key チェックより前に移動する変更を加える場合、既存の `list` サブコマンドが master key 不要になる副作用を意識的に受け入れること（機能的には正しい変更）

## 関連文書
- [ADR-004: 1Passwordシークレット取得に `op read` を使用する](004-1password-op-read-over-op-run.md)
- `src/bswitch/keys.py` — `resolve_api_key()` の実装（env var → 1Password の解決順序）

## Notes
- **著者**: ice1203
- **バージョン**: 0.1
- **変更ログ**:
  + 0.1: 初版作成（Claude Code ステータスライン整合チェックのニーズを契機に記録）
