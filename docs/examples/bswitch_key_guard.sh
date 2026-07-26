#!/usr/bin/env bash
set -euo pipefail
INPUT=$(cat)

# ─────────────────────────────────────────────────────────────────────
# bswitch キー整合性ガード（PreToolUse）
# Backlog APIを叩く可能性のあるBashコマンド / Backlog MCPツールの実行前に
# `bswitch check` を実行し、BACKLOG_API_KEY と state.json（実際の付与状態）の
# 不整合を検出したら deny する。
# 目的: 意図しないキーでの権限エラーと、その後のAIの誤リカバリー
# （権限のある別プロジェクトへの書き込み等）を未然に防ぐ。
# ─────────────────────────────────────────────────────────────────────

BSWITCH="$HOME/.local/bin/bswitch"

TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')

if [[ "$TOOL_NAME" == "Bash" ]]; then
  COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')
  # Backlog APIを叩く可能性のあるコマンドのみ対象:
  #   bee CLI / backlog.jp|com へのURL / curl・wget + backlogホスト / BACKLOG_API_KEY参照
  if ! echo "$COMMAND" | grep -qE '(^|[;&| ] *)bee( |$)|https?://[^ ]*backlog\.(jp|com)|(^|[;&| ] *)(curl|wget) .*backlog\.(jp|com)|BACKLOG_API_KEY'; then
    exit 0
  fi
fi
# Bash以外（matcher側で mcp__.*backlog.* に絞られたMCPツール）は常にチェック

# bswitch未導入環境では何もしない
[[ -x "$BSWITCH" ]] || exit 0

deny() {
  jq -n --arg reason "$1" '{
    hookSpecificOutput: {
      hookEventName: "PreToolUse",
      permissionDecision: "deny",
      permissionDecisionReason: $reason
    }
  }'
  exit 0
}

# checkはJSONをstderrに出す（stdoutはシェル関数のeval対象のため常に空）。
# check自体の失敗（config未作成等）はガード対象外としてスキップ。
CHECK_JSON=$("$BSWITCH" check 2>&1 >/dev/null) || exit 0
echo "$CHECK_JSON" | jq -e 'type == "array"' >/dev/null 2>&1 || exit 0

GRANT_COUNT=$(echo "$CHECK_JSON" | jq 'length')
NG_COUNT=$(echo "$CHECK_JSON" | jq '[.[] | select(.status == "MISMATCH" or .status == "NOT_SET")] | length')

if [[ "$GRANT_COUNT" == "0" ]]; then
  if [[ -n "${BACKLOG_API_KEY:-}" ]]; then
    deny "🚫 bswitch: 付与記録（state.json）が0件なのに BACKLOG_API_KEY が環境変数に残留しています。古い/解除済みプロファイルのキーの可能性が高く、Backlogアクセスは権限エラーになります。Backlogへのアクセスを中止し、ユーザーに『bswitch switch <profile> の実行』を依頼してください。権限のある別のプロジェクトやキーで勝手に再試行してはいけません。"
  fi
  exit 0
fi

if [[ "$NG_COUNT" != "0" ]]; then
  DETAIL=$(echo "$CHECK_JSON" | jq -c .)
  deny "🚫 bswitch check がキー不整合を検出しました: ${DETAIL}。このセッションの BACKLOG_API_KEY は現在の付与状態（state.json）と一致していません（別ターミナルでの切り替え等が原因）。Backlogへのアクセスを中止し、ユーザーに『このターミナルで bswitch switch <profile> を再実行』を依頼してください。権限のある別のプロジェクトやキーで勝手に再試行してはいけません。"
fi

exit 0
