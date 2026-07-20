# ADR-004: 1Passwordシークレット取得に `op read` を使用する（`op run` は使わない）

## ステータス
承諾済み

## 日付
2026-07-20

## コンテキスト
bswitch は `BSWITCH_MASTER_API_KEY` 環境変数で Backlog API キーを受け取る。
この API キーを 1Password から安全に取得してbswitchに渡す wrapper シェル関数が推奨セットアップである。

1Password CLI には、シークレットを渡す方法として主に以下の2つがある:

- `op run`: コマンドを子プロセスとしてラップし、`op://` URI を含む環境変数を展開して渡す
- `op read`: シークレット値を標準出力に出力する単純なコマンド

bswitch の主要機能（`switch` サブコマンドの対話選択UI）は InquirerPy のファジーセレクターに依存しており、ターミナルの正常動作が必須。`op run` でbswitchをラップしたところ、TTY パススルーが壊れ InquirerPy の描画が文字化けして機能しなくなることが判明した。

### 検討した選択肢

1. **`op run --env-file` でbswitchをラップして実行**
   - 概要: `op run` がシークレットを環境変数に展開した状態で bswitch を子プロセスとして起動する
   - メリット: API キーがシェル変数に格納されないため、より安全（ps や coredump に露出しにくい）
   - デメリット: `op run` は子プロセスを独自のプロセスグループで管理するため TTY パススルーが壊れる。InquirerPy のファジーセレクターが文字化けして動作不能になる

2. **`op read` でシークレットを取得し、インライン環境変数でbswitchを直接実行**
   - 概要: `op read` で API キー文字列をシェル変数に格納し、`BSWITCH_MASTER_API_KEY="$api_key" command bswitch "$@"` で直接実行する
   - メリット: TTY パススルーが維持され、InquirerPy の UI が正常動作する
   - デメリット: API キーが一時的にシェル変数 `$api_key` に格納される（メモリ上に文字列が残る可能性）

## 決定
私たちは **`op read` でシークレットを取得し、インライン環境変数でbswitchを直接実行する** 方法を使用します。

### 決定理由
- bswitch の中核機能である対話選択UIが InquirerPy に依存しており、`op run` を使うとその機能が完全に壊れる
- ツールとして正常に動作することが、シェル変数に一時的に格納されるセキュリティリスクより優先される
- `op read` 取得失敗時に `|| return $?` で wrapper 関数を即座に終了させることで、API キーが未設定のまま bswitch が起動する事故を防止できる

## 結果

### ポジティブ
- `switch` コマンドの InquirerPy ファジーセレクターが正常動作する
- TTY パススルーが維持され、ターミナルUIの文字化けが解消する
- wrapper 関数のコードがシンプルになり、読みやすい

### ネガティブ
- API キーがシェル変数 `$api_key` に一時格納される（`op run` 方式と比較してセキュリティ上の注意点）

### トレードオフ
- `op run` は API キーをシェル変数に残さずプロセスの環境変数に直接渡すが、その代償として TTY を破壊する。bswitch のユースケース（ターミナルインタラクティブUI）ではUIの正常動作を優先し、シェル変数への一時格納を受け入れる

## コンプライアンス
- 1Password経由でシークレットを取得する wrapper 関数では `op run` を使わないこと
- `op read "op://<Vault>/<Item>/credential"` でシークレットを取得し、インライン環境変数としてbswitchに渡すこと
- `op read` の失敗は `|| return $?` で wrapper 関数を即座に終了させること

## 関連文書
- [ADR-001: レートリミット対応の権限切替設計](001-rate-limit-aware-permission-switching.md)
- `.claude/skills/bswitch-setup/SKILL.md` — 推奨セットアップ手順（wrapper 関数の具体的な記述を含む）

## Notes
- **著者**: ice1203
- **バージョン**: 0.1
- **変更ログ**:
  + 0.1: 初版作成（PR #11 の変更を契機に記録）
