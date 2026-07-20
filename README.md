# bswitch

Backlog権限スイッチャーCLI。Grantedの `assume` と同じ手触りで、「必要なプロジェクトに・必要な権限だけで・必要なときだけ」仮想ユーザー（サービスアカウント）をプロジェクトへ参加させる個人用ツールです。

## 1. 概要

Backlogでは「何ができるか（閲覧のみ/編集可）」はユーザーアカウント自体のスペースレベル設定で決まり、プロジェクト単位で読取専用の指定はできません。マスターユーザー（人間）がAPIで操作できるレバーは「プロジェクトへの参加/除名」だけです。

そこで強さの異なる仮想ユーザー（サービスアカウント）を2つ用意し、**どちらを参加させるかで権限レベルを表現**します。

| 仮想ユーザー | 種別 | 制限 | 用途 |
|---|---|---|---|
| reader | 一般ユーザー | 課題の閲覧のみ | `permission = read` |
| writer | 一般ユーザー | 制限なし | `permission = write` / `admin` |

`bswitch switch <profile>` を実行すると:

1. config定義済みの**全プロジェクト**から両仮想ユーザーを除名（排他動作）
2. 対象プロジェクトへ、permissionに応じた仮想ユーザーだけを参加させる
3. 現在のシェルに `BACKLOG_API_KEY` / `BACKLOG_SPACE` / `BACKLOG_DOMAIN` / `BACKLOG_PROJECT` を反映する

APIキーを常時保持させてよい構造です。キー自体の能力が「参加状況」で絞られるため、常時強い権限のキーを渡すより安全になります。

## 2. セットアップ手順

### 2.1 インストール

**推奨: リポジトリをクローンせずにインストール**

```bash
uv tool install git+https://github.com/ice1203/backlog-switcher
```

以後 `bswitch` コマンドをPATH上のどこからでも直接実行できます。

```bash
bswitch list
bswitch switch customer-a
```

**開発用: editable install**

```bash
cd /path/to/backlog-switcher
uv tool install -e .
```

ソースコードを変更すればすぐ反映されます。

### 2.2 config作成

`~/.config/backlog-switcher/config`（INI形式）を作成します。パスは環境変数 `BSWITCH_CONFIG` で上書き可能です。

```ini
[default]
space = your-space.backlog.jp      ; スペースのホスト名
writer_user = svc-writer@example.com   ; 制限なし仮想ユーザー（メールアドレスまたは数値ID）
reader_user = svc-reader@example.com   ; 「課題の閲覧のみ」仮想ユーザー（同上）
writer_api_key_ref = op://MyVault/backlog-svc-writer/credential  ; 1Password参照
reader_api_key_ref = op://MyVault/backlog-svc-reader/credential
default_duration = 8h       ; 任意。--duration未指定時の有効期限（未設定なら8h）

[profile customer-a]
project = CUSTOMER_A        ; Backlogプロジェクトキー
permission = read           ; read | write | admin

[profile customer-b]
project = CUSTOMER_B
permission = write
```

configが存在しない場合、`bswitch` はサンプルconfigをstderrに表示して終了します（自動生成はしません）。

### 2.3 マスターAPIキーの設定

`BSWITCH_MASTER_API_KEY` にマスターユーザー（人間）自身のBacklog APIキーを設定します。bswitchが読む唯一の入力用キーです。

```bash
export BSWITCH_MASTER_API_KEY="..."
```

### 2.4 シェル統合の追記

`.zshrc` に以下を追記します（詳細は「5. シェル統合のセットアップ手順」）。

```zsh
eval "$(command bswitch shell-init zsh)"
```

## 3. 使い方

`bswitch` の対話UI・警告・エラー・`list`/`status` の表示結果はすべてstderrに出力されます。stdoutには（シェル関数がevalする）export/unset行のみが出力されます。

### 3.1 前提

```bash
# 1. マスターキー（必須。bswitchの全操作に使う）
export BSWITCH_MASTER_API_KEY="<マスターユーザーのAPIキー>"

# 2. 仮想ユーザーキー（op://を使わない場合）
export BACKLOG_WRITER_API_KEY="<writerのAPIキー>"
export BACKLOG_READER_API_KEY="<readerのAPIキー>"

# 3. シェル統合（.zshrcに追記すると、switch後に環境変数が自動セットされる）
eval "$(command bswitch shell-init zsh)"
```

### 3.2 基本フロー

```bash
# どんなプロファイルがあるか確認
bswitch list
```

```bash
# プロジェクトAを読み取り専用で操作したい
bswitch switch customer-a-read

# → BACKLOG_API_KEY にreaderのキーがセットされる
# → BACKLOG_PROJECT に CUSTOMER_A がセットされる
# → 環境変数を参照するツールはこれを使ってBacklogにアクセス
```

```bash
# 書き込みが必要になった → 切り替え
bswitch switch customer-a-write

# → 自動で reader が除名され、writer が参加する（排他動作）
# → BACKLOG_API_KEY が writer のキーに変わる
```

```bash
# 作業終了 → 参加中のプロジェクトから仮想ユーザーを除名
bswitch release

# → BACKLOG_API_KEY 等がunsetされる
```

### 3.3 便利な機能

```bash
# どのプロファイル使うか迷ったら → 対話UIで選べる
bswitch              # 引数なしで実行すると対話選択モード（bswitch switch と同じ）

# 期限付き（2時間後に自動解除対象になる）
bswitch switch customer-a-read --duration 2h

# 今の状態を確認
bswitch status

# 期限切れの付与を手動で解除
bswitch enforce
```

### 3.4 ポイント

- `switch` するたびに**前のプロファイルは自動で解除**されます（排他動作）。手動で `release` してから `switch` する必要はありません。
- `release` は「参加中の付与を外す」コマンドです。作業終了時に使います。state.jsonが消えた等で残留参加が疑われる場合は `bswitch release --all` でconfig定義済みの全プロジェクトを走査して回収できます。
- シェル統合（`eval "$(command bswitch shell-init zsh)"`）を入れないと、`BACKLOG_API_KEY` 等の環境変数は実際にはセットされません（stdoutに出力されるだけです）。
- `--duration` を指定しなくても、configに `default_duration = 8h` と書いておけば自動で期限付きになります。

### 3.5 サブコマンド一覧

#### `bswitch switch [profile...]`

仮想ユーザーを対象プロジェクトへ参加させます（排他動作: 先に前回付与分（state.jsonに記録されたプロジェクト）から仮想ユーザー2人を除名してから、選択分を参加させます）。

引数なしの `bswitch` は `bswitch switch`（対話選択モード）のショートカットとして動作します。

```bash
# プロファイル名を直接指定
bswitch switch customer-a

# 複数プロファイルを同時付与（read系とwrite/admin系の混在は不可。write+adminの混在は可）
bswitch switch customer-b customer-c

# 引数なし: インタラクティブ選択UI（fuzzy filter付き単一選択）
bswitch              # ショートカット
bswitch switch       # 同じ動作

# --multi: チェックボックス式の複数選択UI
bswitch switch --multi

# 有効期限付き付与（30s/30m/2h/8h形式）
bswitch switch customer-a --duration 2h
```

複数プロファイルを選択した場合、`BACKLOG_PROJECT` はセットされません（`unset`）。単一プロジェクトに決め打ちすると、外部ツールが誤ったプロジェクトをデフォルト扱いする事故につながるためです。

#### `bswitch release [--all]`

state.jsonに記録された参加中プロジェクトから両仮想ユーザーを除名し、環境変数をunsetします。付与記録が0件ならAPIは呼びません。

`--all` を付けると、config定義済みの全プロジェクトを走査して除名します。state.json消失等で通常のreleaseから漏れた残留参加を回収する回復経路です（プロジェクト数×4回の更新系API呼び出しを消費するため、通常は不要です）。

```bash
bswitch release        # 参加中プロジェクトのみ（通常はこちら）
bswitch release --all  # config全プロジェクトを走査（回復用）
```

#### `bswitch status`

各プロファイルのプロジェクトについて、仮想ユーザーの参加状況・権限・有効期限を表示します。

```bash
bswitch status
```

#### `bswitch list`

configのプロファイル一覧（プロファイル名・プロジェクトキー・権限）を表示します。API呼び出しは行いません。

```bash
bswitch list
```

#### `bswitch enforce`

期限切れの付与だけを解除します（定期実行用）。解除対象がなければ何もせず終了コード0です。他の全サブコマンドも冒頭で同じ処理（遅延強制）を自動的に行うため、通常は明示的に呼ぶ必要はありません。定期実行のセットアップは「6. launchdによるenforce定期実行の手動セットアップ手順」を参照してください。

```bash
bswitch enforce
```

#### `bswitch shell-init zsh`

シェル統合用の関数定義を出力します（`.zshrc` にeval経由で読み込む）。詳細は次章。

## 4. 仮想ユーザーキーの1Password保管と `op://` 参照の設定手順

1. 1Password CLI（`op`）をインストールし、ログインしておきます（`op signin`）。
2. writer/reader それぞれの仮想ユーザーAPIキーを1Passwordのボールト（例: `MyVault`）に保存します。

   ```bash
   op item create --category="API Credential" \
     --vault MyVault \
     --title "backlog-svc-writer" \
     credential="<writerのAPIキー>"

   op item create --category="API Credential" \
     --vault MyVault \
     --title "backlog-svc-reader" \
     credential="<readerのAPIキー>"
   ```

3. configの `writer_api_key_ref` / `reader_api_key_ref` に `op://` 参照を書きます。

   ```ini
   writer_api_key_ref = op://MyVault/backlog-svc-writer/credential
   reader_api_key_ref = op://MyVault/backlog-svc-reader/credential
   ```

4. 動作確認:

   ```bash
   op read op://MyVault/backlog-svc-writer/credential
   ```

   値が表示されればOKです。bswitch実行時は `subprocess.run(["op", "read", ref])` で都度取得され、ファイルには一切書き出されません。

### テスト・非1Password環境用フォールバック

環境変数 `BACKLOG_WRITER_API_KEY` / `BACKLOG_READER_API_KEY` が設定されていれば、`op read` を呼ばずそちらを優先して使います（E2Eテスト等で使用）。

## 5. シェル統合のセットアップ手順

Grantedと同様、子プロセスは親シェルの環境変数を変更できないため、シェル関数ラッパー方式で実現しています。

1. `.zshrc` に以下を追記します。

   ```zsh
   eval "$(command bswitch shell-init zsh)"
   ```

2. シェルを再読み込みします。

   ```bash
   source ~/.zshrc
   ```

3. これで `bswitch` はシェル関数として定義されます。`bswitch switch <profile>` を実行すると、内部で `command bswitch "$@"` を実行して標準出力（export/unset行のみ）を `eval` し、現在のシェルに環境変数が反映されます。

## 6. launchdによるenforce定期実行の手動セットアップ手順

`--duration` 付きで付与した権限は、どのサブコマンドを実行しても冒頭で自動的に期限切れチェック（遅延強制）が働きますが、しばらくbswitchを実行しない場合に備えて `bswitch enforce` を定期実行しておくと安全です。自動インストールは提供しないため、以下の手順で手動セットアップしてください。

**重要**: `BSWITCH_MASTER_API_KEY` をplist等のファイルに直接書かないでください。plistから起動するラッパースクリプトが、実行時にmacOS KeychainまたはFile 1Password CLI経由でキーを取得する構成にします。

### 6.1 方式A: macOS Keychainを使う

1. マスターAPIキーをKeychainに登録する（一度だけ、手動で実行）。

   ```bash
   security add-generic-password -a "$USER" -s bswitch-master-api-key -w "<マスターAPIキー>"
   ```

2. ラッパースクリプトを作成する（`~/.local/bin/bswitch-enforce.sh`）。

   ```bash
   #!/bin/zsh
   set -euo pipefail
   export BSWITCH_MASTER_API_KEY="$(security find-generic-password -a "$USER" -s bswitch-master-api-key -w)"
   exec uv run --project /path/to/backlog-switcher bswitch enforce
   ```

   ```bash
   chmod +x ~/.local/bin/bswitch-enforce.sh
   ```

3. launchd用plistを作成する（`~/Library/LaunchAgents/com.example.bswitch-enforce.plist`）。**APIキーの値はplistに一切含めません。**

   ```xml
   <?xml version="1.0" encoding="UTF-8"?>
   <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
   <plist version="1.0">
   <dict>
       <key>Label</key>
       <string>com.example.bswitch-enforce</string>
       <key>ProgramArguments</key>
       <array>
           <string>/Users/<username>/.local/bin/bswitch-enforce.sh</string>
       </array>
       <key>StartInterval</key>
       <integer>300</integer>
       <key>StandardOutPath</key>
       <string>/tmp/bswitch-enforce.log</string>
       <key>StandardErrorPath</key>
       <string>/tmp/bswitch-enforce.log</string>
   </dict>
   </plist>
   ```

4. 読み込んで有効化する。

   ```bash
   launchctl load ~/Library/LaunchAgents/com.example.bswitch-enforce.plist
   ```

5. 停止・削除する場合。

   ```bash
   launchctl unload ~/Library/LaunchAgents/com.example.bswitch-enforce.plist
   ```

### 6.2 方式B: 1Password CLIを使う

ラッパースクリプトを以下のように変更するだけで、Keychainの代わりに1Password CLIからマスターキーを取得できます（1Password CLIがデスクトップアプリ連携でロック解除済みであることが前提）。

```bash
#!/bin/zsh
set -euo pipefail
export BSWITCH_MASTER_API_KEY="$(op read op://MyVault/backlog-master-api-key/credential)"
exec uv run --project /path/to/backlog-switcher bswitch enforce
```

plistの設定手順は方式Aと同じです（`ProgramArguments` が指すスクリプトの中身だけが異なります）。

## 開発

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest              # 単体テスト（実APIは呼ばない）
BSWITCH_E2E=1 BSWITCH_E2E_SPACE=... BSWITCH_MASTER_API_KEY=... uv run pytest tests/e2e/
```
