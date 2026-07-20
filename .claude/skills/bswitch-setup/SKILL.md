---
name: bswitch-setup
description: |
  backlog-switcher (bswitch) の初回セットアップ・config更新を対話的に行う。
  インストール、BSWITCH_MASTER_API_KEY 確認、Backlog API でユーザー/プロジェクト一覧取得、
  config 生成・更新、シェル統合まで一気通貫でガイドする。
  「bswitchをセットアップしたい」「bswitchのconfigを更新したい」「プロファイルを追加したい」と言われたら必ず使用する。
argument-hint: "[setup|update]"
---

# bswitch セットアップスキル

backlog-switcher の初回セットアップ（`setup`）と config 更新（`update`）を対話的にガイドする。

## 引数

| 引数 | 意味 |
|---|---|
| `setup`（デフォルト）| 初回インストールから config 作成・シェル統合まで |
| `update` | 既存 config へのプロファイル追加・変更 |

引数なし・判断できない場合は既存 config の有無で自動判定する。

---

## ファイル操作の原則

ファイル・設定の書き込みが発生するすべてのステップで以下を守る:

1. **事前に内容を提示して了承を取る** — 「以下の内容を `{パス}` に書き込みます。よいですか？」と確認してから実行する
2. **書き込み失敗時はユーザーに委譲** — Claude が書き込めない場合（権限エラー等）は、実行すべきコマンドをそのまま提示してユーザーに実行してもらう
3. **実行系コマンド（インストール等）も同様** — `uv tool install` など副作用のあるコマンドは事前に提示・了承確認してから Claude が実行する

---

## 実行フロー

### Step 0. モード判定

既存 config のパスを確認する:

```bash
echo "${BSWITCH_CONFIG:-$HOME/.config/backlog-switcher/config}"
```

そのパスが存在すれば `update` モード、なければ `setup` モードで進む。
引数で明示された場合はそちらを優先する。

---

### Step 1. インストール確認（setup モードのみ）

`bswitch --help` を実行して既にインストール済みか確認する。

未インストールの場合、以下をユーザーに実行してもらう:

```bash
uv tool install git+https://github.com/ice1203/backlog-switcher
```

実行完了を確認してから次へ進む。`bswitch --help` が通れば成功。

---

### Step 2. BSWITCH_MASTER_API_KEY 確認

```bash
echo "${BSWITCH_MASTER_API_KEY:+(set)}"
```

未設定の場合、1Password を使うかどうかをユーザーに確認し、`~/.zshrc` への追記内容を案内する。

#### 1Password を使う場合（推奨）

以下の wrapper 関数を `~/.zshrc` に追記する。これがシェル統合も兼ねるため `eval "$(command bswitch shell-init zsh)"` は**不要**。

```bash
bswitch() {
  local api_key
  api_key=$(op read "op://<Vault>/<Item>/credential") || return $?  # Vault名・項目名は各自の構成に合わせて変更
  local output
  output=$(BSWITCH_MASTER_API_KEY="$api_key" command bswitch "$@")
  local exit_code=$?
  if [[ -n "$output" ]]; then
    eval "$output"
  fi
  return $exit_code
}
```

1Password の項目は以下で作成する:
- **項目名**: 任意（例: `My Backlog API Key`）— Vault 名・項目名は上記の参照パスに合わせて変更
- **フィールド名**: `credential`
- **値**: Backlog の個人設定画面で発行した API キー

#### 1Password を使わない場合

以下を `~/.zshrc` に追記する。`BSWITCH_MASTER_API_KEY` は direnv 等プロセス単位で注入できる方法で別途設定すること（`export` による常駐は非推奨）。

```bash
eval "$(command bswitch shell-init zsh)"
```

---

追記内容を提示して了承を得てから書き込む。Claude が書き込めない場合はユーザーに実行を依頼する。
追記後: `source ~/.zshrc` で即時反映できることを伝える。

---

### Step 3. Claude 再起動（setup モードのみ）

Step 2 で `~/.zshrc` に追記した内容を反映するため、ユーザーに以下を依頼する:

```
.zshrc の設定を反映するため、一度 Claude を終了して `claude -c` で再起動してください。
再起動後に続きから再開できます。
```

再起動後、`BSWITCH_MASTER_API_KEY` が有効かを確認してから次へ進む:

```bash
echo "${BSWITCH_MASTER_API_KEY:+(set)}"
```

`(set)` と表示されれば OK。

---

### Step 4. スペースホスト名の確認

`update` モードで既存 config に `space =` があれば自動取得する。

なければ:

```
Backlog スペースのホスト名を入力してください（例: your-space.backlog.jp）:
```

以降 `SPACE` 変数として保持する。

---

### Step 5. 仮想ユーザーのメールアドレス入力

writer / reader ユーザーのメールアドレスを直接入力してもらう:

```
writer ユーザーのメールアドレスを入力してください:
reader ユーザーのメールアドレスを入力してください（不要の場合はスキップ）:
```

入力されたメールアドレスが実際に存在するか API で確認する:

```bash
curl -s "https://${SPACE}/api/v2/users?apiKey=${BSWITCH_MASTER_API_KEY}" \
  | python3 -c "
import json, sys
email = '${INPUT_EMAIL}'
users = json.load(sys.stdin)
match = [u for u in users if u.get('mailAddress') == email]
if match:
    print(f\"OK: id={match[0]['id']} name={match[0]['name']}\")
else:
    print('NOT FOUND')
"
```

NOT FOUND の場合はメールアドレスの再入力を促す。
reader をスキップした場合、config の `reader_user` / `reader_api_key_ref` 行は省略する。

---

### Step 6. API キー設定方法の確認

```
仮想ユーザーの API キーをどのように管理しますか？

1. 環境変数（BACKLOG_WRITER_API_KEY / BACKLOG_READER_API_KEY）
2. 1Password 参照（op://Vault/Item/credential 形式）
3. 後で手動設定する（config にプレースホルダーを入れる）
```

選択に応じて config の `writer_api_key_ref` / `reader_api_key_ref` を決定する:

| 選択 | config の値 |
|---|---|
| 1 (環境変数) | 行を省略（env fallback が使われる） |
| 2 (1Password) | `writer_api_key_ref = op://MyVault/backlog-svc-writer/credential` |
| 3 (後で) | `writer_api_key_ref = FIXME` |

#### 選択 2（1Password）の場合

以下の手順で 1Password に項目を作成するよう案内する:

**writer 用**
- **項目名**: 任意（例: `Backlog Writer API Key`）— 後述の参照パスに合わせる
- **フィールド名**: `credential`
- **値**: Backlog の writer 仮想ユーザーの API キー

**reader 用**（reader を設定する場合）
- **項目名**: 任意（例: `Backlog Reader API Key`）
- **フィールド名**: `credential`
- **値**: Backlog の reader 仮想ユーザーの API キー

作成後、以下をそれぞれ質問して `op://Vault/Item/Field` 形式を組み立てる:

```
Vault 名を入力してください（例: Personal）:
writer の項目名を入力してください（例: Backlog SVC Writer）:
フィールド名を入力してください（デフォルト: credential）:
```

reader も同様に確認する（reader を設定した場合のみ）。

入力値から参照パスを組み立てて提示し、確認を取る:
```
writer_api_key_ref = op://{Vault}/{Item}/{Field}
```

---

### Step 7. Backlog プロジェクト一覧取得

```bash
curl -s "https://${SPACE}/api/v2/projects?apiKey=${BSWITCH_MASTER_API_KEY}" \
  | python3 -c "
import json, sys
projects = json.load(sys.stdin)
for p in projects:
    print(f\"{p['projectKey']}  {p['name']}\")
"
```

取得した結果を使って、**Claude 自身のテキスト出力として**全件を番号付きで出力する（ツール実行結果に頼ってはならない。ツール出力は UI 上で折りたたまれており、ユーザーには見えない）。

出力形式:

```
以下のプロジェクトが見つかりました。追加したい番号を入力してください（複数はカンマ区切り）:

  1. KEY_A                    プロジェクトA
  2. KEY_B                    プロジェクトB
  ...（省略しない。全件出力する）

番号を入力してください（例: 1,3,5）:
```

入力された番号からプロジェクトキーを解決する。

permission ごとのプロファイルを自動生成して提示する。確認は取らず、案を見せてから修正があれば聞く。

**生成ルール**:
- writer が設定されている場合 → `write` と `admin` のプロファイルを生成
- reader が設定されている場合 → `read` のプロファイルを生成
- プロファイル名 = プロジェクトキーを小文字・アンダースコアをハイフンに変換 + `-read` / `-write` / `-admin` サフィックス（例: `PROJ_A` → `proj-a-read`, `proj-a-write`, `proj-a-admin`）

全プロジェクト分をまとめて提示して確認を取る:

```
以下の内容で config に追加します。よいですか？

[profile proj-a-read]
project = PROJ_A
permission = read

[profile proj-a-write]
project = PROJ_A
permission = write

[profile proj-a-admin]
project = PROJ_A
permission = admin

...（全プロジェクト分）
```

---

### Step 8. config 生成・更新

生成内容が決まったら、書き込む前に全文をユーザーに提示して了承を取る:

```
以下の内容を `{configパス}` に書き込みます。よいですか？

--- (内容) ---
```

了承後に書き込む。Claude が書き込めない場合は以下をユーザーに提示して実行を依頼する:

```bash
mkdir -p ~/.config/backlog-switcher
cat > ~/.config/backlog-switcher/config << 'EOF'
(内容)
EOF
```

**setup モード**: `~/.config/backlog-switcher/` ディレクトリを作成し config を新規作成。

**update モード**: 既存 config を `$TMPDIR` にバックアップしてから、新規プロファイルを追記。既存プロファイルと同名が指定された場合は上書き確認をしてから変更。

生成する config の形式:

```ini
[default]
space = {SPACE}
writer_user = {writer のメールアドレスまたは数値ID}
reader_user = {reader のメールアドレスまたは数値ID}  ; reader スキップ時は省略
writer_api_key_ref = {Step 5 で決定した値}
reader_api_key_ref = {Step 5 で決定した値}          ; reader スキップ時は省略
default_duration = 8h

[profile {プロファイル名}]
project = {プロジェクトキー}
permission = {read|write|admin}
```

**config ファイルへの API キーの直接書き込みは禁止。**

書き込み後に `bswitch list` を実行して正常に読み込まれることを確認する。

---

### Step 9. 完了サマリ

```
✅ セットアップ完了

config: {configパス}
プロファイル: {追加したプロファイル名一覧}

使い方:
  bswitch          # プロファイル選択 → 対象プロジェクトへ参加
  bswitch list     # プロファイル一覧
  bswitch status   # 現在の参加状況・有効期限
  bswitch release  # 作業後のクリーンアップ
```

config に `FIXME` が残っている場合はここで警告する。

---

### Step 10. ステータスライン統合（任意）

以下を案内して、希望すれば設定を手伝う:

```
bswitch check の結果を Claude Code のステータスラインや starship・tmux に表示できます。
サンプルスクリプトが docs/examples/bswitch-statusline.js にあります。
セットアップしますか？
```

Claude Code の `statusline.js`・tmux・starship などから `node bswitch-statusline.js` を呼び出して使う構成を案内する。スクリプトは `docs/examples/bswitch-statusline.js` にある。ユーザーの環境に応じて配置場所を決めてもらい、各ツールの設定ファイルから呼び出す形を一緒に確認する。
