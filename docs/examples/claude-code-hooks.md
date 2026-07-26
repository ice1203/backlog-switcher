# Claude Code hook 連携

bswitch を Claude Code の hook と組み合わせ、AI エージェントが「意図しないプロジェクトへ書き込む」事故を2段構えで防ぐためのサンプルと導入手順です。

| hook | スクリプト | 役割 |
|---|---|---|
| `PreToolUse` | `bswitch_key_guard.sh` | キー整合の強制ブロック（fail-closed） |
| `SessionStart` | `bswitch_session_guard.sh` | プロジェクト整合の注意喚起（ブロック不可） |

2つの役割分担と、なぜ両方必要なのかは [README](../../README.md) の「8. Claude Code hook 連携」を参照してください。

## 前提

- `bswitch` が `~/.local/bin/bswitch` にインストールされていること（別パスの場合は各スクリプトの `BSWITCH` 変数を書き換える）
- `bswitch check` が `project` フィールドを出力するバージョンであること。未対応の古いバージョンがインストールされていると、SessionStart hook の注入内容が `project=null` になり整合判定ができない。次のコマンドで確認する

  ```bash
  bswitch check 2>&1 >/dev/null    # 各要素に "project" が含まれること（付与0件のときは [] ）
  ```

- `jq` が使えること
- どちらのスクリプトも `bash` 経由で起動するため、実行権限（`chmod +x`）は不要

## セットアップ

1. スクリプトを配置する。

   ```bash
   mkdir -p ~/.claude/hooks
   cp docs/examples/bswitch_key_guard.sh     ~/.claude/hooks/
   cp docs/examples/bswitch_session_guard.sh ~/.claude/hooks/
   ```

2. `~/.claude/settings.json` の `hooks` へ次を追記する。

   **既存の `hooks.SessionStart` / `hooks.PreToolUse` 配列を上書きせず、要素として追記してください。** 他の hook がすでに登録されている場合、配列ごと置き換えるとそれらが失われます。

   ```json
   "hooks": {
     "PreToolUse": [
       {
         "matcher": "Bash",
         "hooks": [
           {
             "type": "command",
             "command": "bash ~/.claude/hooks/bswitch_key_guard.sh",
             "timeout": 10,
             "statusMessage": "bswitch キー整合性チェック"
           }
         ]
       },
       {
         "matcher": "mcp__.*backlog.*",
         "hooks": [
           {
             "type": "command",
             "command": "bash ~/.claude/hooks/bswitch_key_guard.sh",
             "timeout": 10,
             "statusMessage": "bswitch キー整合性チェック"
           }
         ]
       }
     ],
     "SessionStart": [
       {
         "matcher": "",
         "hooks": [
           {
             "type": "command",
             "command": "bash ~/.claude/hooks/bswitch_session_guard.sh",
             "timeout": 10,
             "statusMessage": "bswitch プロジェクト整合チェック"
           }
         ]
       }
     ]
   }
   ```

3. 追記後に JSON の妥当性を確認する。

   ```bash
   python3 -m json.tool ~/.claude/settings.json > /dev/null && echo OK
   ```

## PreToolUse: `bswitch_key_guard.sh`

### 対象の絞り込み

- `matcher: "Bash"` — Bash ツールの全実行で起動するが、スクリプト内でコマンド文字列を検査し、Backlog API を叩く可能性のあるものだけをチェック対象にする（`bee` CLI / `backlog.jp`・`backlog.com` への URL / `curl`・`wget` + Backlog ホスト / `BACKLOG_API_KEY` の参照）。それ以外は即 `exit 0` で素通り
- `matcher: "mcp__.*backlog.*"` — Backlog MCP ツールを対象にする。この正規表現は `mcp__backlog__*` 形式と、プラグイン経由の `mcp__plugin_<名前>_backlog__*` 形式の両方を包含する

### deny 条件

次のいずれかで deny する。

| 条件 | 意味 |
|---|---|
| `status` が `MISMATCH` の付与がある | このシェルの `BACKLOG_API_KEY` が付与記録と別のキーになっている（別ターミナルでの `switch` など） |
| `status` が `NOT_SET` の付与がある | 付与記録はあるが `BACKLOG_API_KEY` が未設定 |
| 付与0件なのに `BACKLOG_API_KEY` が残留している | 解除済みプロファイルのキーが環境変数に残っている |

deny の理由文には「権限のある別のプロジェクトやキーで勝手に再試行してはいけません」を含めます。権限エラーからの誤ったリカバリー（別プロジェクトへの書き込み）を防ぐためです。

`status` が全て `OK` の場合、および `UNKNOWN` のみの場合はブロックしません（`UNKNOWN` は旧バージョンで付与したグラントに `key_fingerprint` が無いケースで、キーが実際に食い違っているとは限らないため）。

### fail-open

次の場合は判定を諦めて素通りします。ガードの都合で通常の開発作業が止まらないようにするためです。

- `bswitch` が `BSWITCH` のパスに存在しない（未導入環境）
- `bswitch check` が異常終了した（config 未作成など）
- `check` の出力が JSON 配列としてパースできない

## SessionStart: `bswitch_session_guard.sh`

### 注入される内容

stdin の hook 入力 JSON から `cwd` を取り出し、`bswitch check` の結果と合わせて **stdout へ平文テキスト**を出力します。Claude Code は `SessionStart` の stdout をそのままコンテキストへ追加します。

付与が1件以上あるとき、注入されるのは次の3つです。

1. 作業ディレクトリ（`cwd`）と、付与中の各グラント（`profile` / `project` / `permission` / `status`）
2. 「作業ディレクトリがどの案件のものかを把握し、付与中プロジェクトと意味的に対応しているか判定せよ」という指示
3. 判定結果を応答の冒頭に1行で報告させる書式（4パターン。実際の表示例は README を参照）

判定は Backlog を操作するかどうかに関わらず、セッション開始時に1回行わせます。Backlog と無関係な作業をしているセッションでも、作業ディレクトリと付与中プロジェクトのズレに冒頭で気づけることが目的です。

### 出力しないもの

- **全プロファイル一覧（`bswitch list` 相当）は注入しません。** プロファイルが多い環境では毎セッション全プロジェクトキーがコンテキストに載ってしまうためです。判定には付与中の1〜2件で足ります
- API キーの値は一切出力しません（`bswitch check` の出力にキーの値は含まれません）

### 無音になる条件

| 状態 | 出力 |
|---|---|
| 付与0件 かつ `BACKLOG_API_KEY` 未設定 | **何も出力しない**（Backlog を使っていない正常な状態） |
| 付与0件 かつ `BACKLOG_API_KEY` 設定あり | 残留キー警告を注入 |
| 付与1件以上 | 現在状態 + 判断指示を注入 |

`PreToolUse` 側と同じく fail-open です（`bswitch` 未導入 / `check` の異常終了 / JSON 以外の出力 → 無音で `exit 0`）。stderr へは何も出力しません。

`source`（`startup` / `resume` / `clear` / `compact` / `fork`）でのフィルタはしていません。コンパクション後は文脈が失われるため、再注入が有効です。

### 運用上の注意

- 注入されるのは指示であって強制ではないため、判定・報告そのものが実行されない可能性があります
- ディレクトリと Backlog プロジェクトの対応は AI の意味的判断に依存するため非決定的です。対応が読み取れない場合は「判断できません」と報告させ、黙って進ませない設計にしています
- セッション途中で別ターミナルが `switch` しても `SessionStart` は再発火しません。切り替えたら新しいセッションを開くか、`PreToolUse` 側の deny に委ねてください

## 動作確認

hook は stdin から JSON を受け取るため、pipe で単体確認できます。

```bash
IN='{"session_id":"t","transcript_path":"/tmp/t.jsonl","cwd":"/tmp/workdir","hook_event_name":"SessionStart","source":"startup"}'
echo "$IN" | bash ~/.claude/hooks/bswitch_session_guard.sh
```

付与0件・キーなしの状態では何も出力されないこと（`| wc -c` が `0`）を確認してください。
