# 設計ドキュメント

## 目的

AI（Claude Code等）にBacklogを操作させる際、常時強い権限のAPIキーを渡すのは危険。
Grantedの `assume` と同じ手触りで「必要なプロジェクトに・必要な権限だけで・必要なときだけ」AI用仮想ユーザーを参加させるCLIを作り、AIの操作を最小権限に絞る。

## 権限モデルの前提

- Backlogでは「何ができるか（閲覧のみ/編集可）」は**ユーザーアカウント自体のスペースレベル設定**で決まり、プロジェクト単位で読取専用の指定はできない（公式ドキュメント確認済み）
- マスターユーザー（人間）がAPIで操作できるレバーは「**プロジェクトへの参加/除名**」のみ
- 強さの異なるAI用仮想ユーザーを2人用意し、**どちらを参加させるかで権限レベルを表現する**

| 仮想ユーザー | 種別 | 制限 | 用途 |
|---|---|---|---|
| reader | 一般ユーザー | 課題の閲覧のみ | `permission = read` |
| writer | 一般ユーザー | 制限なし | `permission = write` / `admin` |

- AI側は仮想ユーザーのAPIキーを常時保持してよい。キー自体の能力が「参加状況」で絞られるため安全

## 権限レベル

| permission | 操作 |
|---|---|
| `read` | reader を参加させる |
| `write` | writer を参加させる |
| `admin` | writer を参加させ、さらにプロジェクト管理者フラグ付与を**試行**。API権限エラー時は write に縮退して警告表示 |

`admin` の縮退理由: プロジェクト管理者追加APIのドキュメント上の実行可能権限は「管理者」のみだが、UI上はプロジェクト管理者でもフラグを付けられるという実体験報告があり、API挙動は未確定。

## スコープ

### in-scope

- Python製CLI `bswitch`（サブコマンド: `switch` / `release` / `status` / `list` / `enforce` / `shell-init`）
- TTL（自動解除）: 状態ファイル（`state.json`）に付与中のプロファイル・ユーザーID・期限を記録。APIキーは記録しない
- 遅延強制: どのサブコマンドも実行冒頭で期限切れの付与があれば自動解除
- 環境変数エクスポート（Granted風シェル統合）
- 設定ファイル `~/.config/backlog-switcher/config`（INI形式）
- モックによる単体テスト一式
- 実機E2Eテスト（環境変数 `BSWITCH_E2E_*` で指定したテスト用スペース・プロジェクトのみを対象）

### out-of-scope

- 本番スペースでの検証
- launchdタイマーの自動インストール（READMEに手動手順を書くのみ）
- 仮想ユーザーの作成・制限設定の自動化
- 複数スペースの同時管理
- AI側ツール（MCPサーバー等）の設定変更

## 環境変数設計

入力と出力を完全分離する:

| 変数名 | 入力/出力 | 用途 |
|---|---|---|
| `BSWITCH_MASTER_API_KEY` | 入力（必須） | マスターユーザーのキー。bswitchの全API呼び出しに使う |
| `BACKLOG_API_KEY` | 出力専用 | switchが仮想ユーザーのキーを書き込む。bswitch自身は読まない |
| `BACKLOG_WRITER_API_KEY` | 入力（任意） | 設定済みなら `op read` を呼ばずこちらを使う（テスト・非1Password環境用） |
| `BACKLOG_READER_API_KEY` | 入力（任意） | 同上 |
| `BACKLOG_SPACE` | 出力専用 | switch成功後にセット（例: `https://your-space.backlog.jp`） |
| `BACKLOG_DOMAIN` | 出力専用 | switch成功後にセット（例: `your-space.backlog.jp`） |
| `BACKLOG_PROJECT` | 出力専用 | switch成功後にセット。--multiで複数選択時はunset |
| `BSWITCH_CONFIG` | 入力（任意） | configファイルパスの上書き |
| `BSWITCH_E2E` | 入力（任意） | `1` のときのみE2Eテストを実行 |

`BSWITCH_MASTER_API_KEY` 未設定なら分かりやすいエラーで即終了。

## シェル統合の仕組み

子プロセスは親シェルの環境変数を変更できないため、Grantedと同じシェル関数ラッパー方式を採用:

- `.zshrc` に `eval "$(command bswitch shell-init zsh)"` を書くと `bswitch` シェル関数が定義される
- バイナリが出力するexport/unset行をevalする
- 対話UI・通常メッセージはstderr、eval対象はstdoutへ分離

## --multi の仕様

- **read系とwrite/admin系の混在選択は不可**: `BACKLOG_API_KEY` が1つに定まらないため。混在を検出したらエラーを表示し、何も付与せず終了（部分適用しない）
- write と admin の混在は可（どちらも writer のキー）
- 複数プロファイル選択時は `BACKLOG_PROJECT` をunset（空文字も不可）。1件選択時は通常どおりセット

## 技術制約

- Python 3.13+、uv管理（`pyproject.toml`）
- 依存は最小限: httpx（HTTP）、InquirerPy（対話UI）、configparser（INI・標準ライブラリ）
- 外部コマンド依存は1Password CLI（`op`）のみ許可
- 型ヒント必須、ruffでlint/format

## 禁止事項

- APIキーをファイル・ログ・エラーメッセージ・テストコードに書き出さない
- config定義外のプロジェクトに対する参加/除名操作をしない
- マスターユーザー自身のプロジェクト参加状態を変更しない
- 本ツールが呼ぶBacklog APIは「プロジェクトユーザー追加/削除/一覧・プロジェクト管理者追加/削除/一覧・参照系」のみ
- 単体テスト（pytestデフォルト実行）から実APIを呼ばない
- E2Eが操作してよいのは環境変数 `BSWITCH_E2E_SPACE` で指定したスペースの `MY_PROJECT` プロジェクトのみ
- E2E対象以外のスペースへは一切アクセスしない

## 判断指針

- 優先順位: **安全（誤って権限を広げない）> シンプルさ > 機能の豊富さ > 利便性**
- 既存のAWSプロファイル/Grantedの慣習に寄せる
- API呼び出しの失敗は「何をしようとして・どのプロジェクトで・何が返ったか」が分かるメッセージにする（キーは伏せる）
- API呼び出しは安全に再実行可能にする（既に参加済みユーザーの追加、未参加ユーザーの除名で異常終了しない）
